"""Actual worker/runtime processes, installed CLI and official MCP share HTTP."""

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import psutil
import pytest
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters

BASE = "/api/v1/library/catalog/maintenance"
REVISION = "a" * 40


@pytest.fixture
def metadata_gateway(tmp_path, request):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    calls = tmp_path / "runtime-calls.txt"
    parameters = getattr(request, "param", {})
    profile = {
        "name": "deterministic-process-fixture",
        "model": "deterministic-stand-in",
        "command": [
            sys.executable,
            str(ROOT / "tests/fixtures/catalog_metadata_agent.py"),
            "--calls",
            str(calls),
            "--pid-file",
            str(tmp_path / "runtime.pid"),
            "--delay",
            str(parameters.get("runtimeDelay", 0)),
        ],
    }
    environment = os.environ | {
        "SPATIAL_RECORDINGS_ROOT": str(tmp_path / "recordings"),
        "SPATIAL_STATE_ROOT": str(tmp_path / "state"),
        "LW_LIBRARY": str(tmp_path / "library"),
        "LW_URL": f"http://127.0.0.1:{port}",
        "LW_CATALOG_AGENT_PROFILE": json.dumps(profile),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(ROOT / "apps/cli") + os.pathsep + str(ROOT),
    }
    with (tmp_path / "gateway.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "catalog_metadata_gateway:app",
                "--app-dir",
                "tests/fixtures",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            with httpx.Client(base_url=environment["LW_URL"], timeout=10, trust_env=False) as http:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, (tmp_path / "gateway.log").read_text()
                    try:
                        if http.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.02)
                else:
                    raise AssertionError("Owned metadata gateway did not start")
                yield http, environment, calls, tmp_path
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


async def cli(environment, *arguments):
    process = await asyncio.create_subprocess_exec(
        str(LW),
        *arguments,
        "--json",
        env=environment,
        cwd=ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 25)
    assert process.returncode == 0, (stdout, stderr)
    return json.loads(stdout)


async def test_actual_subprocess_worker_and_runtime_receipts_share_cli_mcp_http(metadata_gateway):
    http, environment, calls, directory = metadata_gateway
    assert http.patch(BASE + "/collections/simready", json={"enabled": True}).status_code == 200
    run = await cli(environment, "catalog", "maintain", "simready", "--revision", REVISION, "--wait")
    assert run["status"] == "succeeded", run
    assert run["coverage"]["indexed"] == run["coverage"]["organized"] == 2
    assert run["usage"]["modelCalls"] == 1 and run["usage"]["modelTokens"] == 10
    assert run["usage"]["requests"] >= 3 and run["usage"]["networkBytes"] > 0
    assert len(calls.read_text().splitlines()) == 1
    expected = http.get(BASE + "/runs/" + run["id"]).json()
    assert await cli(environment, "catalog", "maintenance-status", "--id", run["id"]) == expected
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    async with Client(transport, mode="legacy") as mcp:
        assert await result(mcp, "catalog_maintenance_status", id=run["id"]) == expected
        found = await result(mcp, "catalog_search", query="夹具", scope="online", category="container")
        assert found["counts"]["matching"] == 2
        pinned = await result(
            mcp,
            "catalog_search",
            query="夹具",
            scope="online",
            category="container",
            release=found["snapshot"]["id"],
        )
        cli_pinned = await cli(
            environment,
            "catalog",
            "search",
            "-q",
            "夹具",
            "--scope",
            "online",
            "--category",
            "container",
            "--release",
            found["snapshot"]["id"],
        )
        assert abs(cli_pinned["snapshot"]["ageSeconds"] - pinned["snapshot"]["ageSeconds"]) <= 5
        cli_pinned["snapshot"].pop("ageSeconds")
        pinned["snapshot"].pop("ageSeconds")
        assert cli_pinned == pinned
        assert await result(mcp, "catalog_collections") == http.get(BASE + "/collections").json()
    # Same inputs reuse organized results without another runtime execution.
    repeated = await cli(environment, "catalog", "maintain", "simready", "--revision", REVISION, "--wait")
    assert repeated["status"] == "succeeded" and repeated["coverage"]["reused"] == 2
    assert repeated["usage"]["modelCalls"] == 0 and repeated["usage"]["modelTokens"] is None
    assert len(calls.read_text().splitlines()) == 1
    requests = http.get("/e2e/catalog-metadata-source").json()["requests"]
    assert not any(path.endswith((".usd", "normal.png", "preview.png")) for path in requests)
    assert not (directory / "state/catalog-metadata/metadata.sqlite3").parent.joinpath("runtime").exists()


async def test_external_agent_handoff_and_local_thumbnails_use_real_worker_cli_and_mcp(metadata_gateway):
    http, environment, calls, directory = metadata_gateway
    assert http.patch(BASE + "/collections/simready", json={"enabled": True}).status_code == 200
    run = await cli(environment, "catalog", "maintain", "simready", "--revision", REVISION,
                    "--organizer", "external", "--previews", "--wait")
    assert run["phase"] == "awaiting_agent" and run["sourceWorkerComplete"], run
    assert not calls.exists()  # A configured runtime is not invoked in external mode.
    assert run["previewCoverage"]["available"] == 2, run
    batch = await cli(environment, "catalog", "batch-claim", run["id"])
    assert batch["vocabulary"]["categories"] and any(item.get("text") for item in batch["evidence"])
    payload = {
        "inputKey": batch["inputKey"], "nativeSessionId": "deterministic-external-fixture",
        "usage": {"totalTokens": 10}, "execution": {"kind": "deterministic-process-fixture"},
        "results": [{"upstreamId": item["upstreamId"], "inputDigest": item["inputDigest"],
                     "fields": {"summary": {"zh": "用于试剂暂存的容器候选"}, "uses": ["试剂暂存"]}}
                    for item in batch["entries"]],
    }
    document = directory / "external-receipt.json"
    document.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    await cli(environment, "catalog", "batch-submit", run["id"], batch["id"], str(document))
    finished = await cli(environment, "catalog", "maintenance-finish", run["id"])
    assert finished["status"] == "succeeded", finished
    found = await cli(environment, "catalog", "metadata-search", "--q", "试剂暂存")
    entry = found["results"][0]
    before = http.get("/e2e/catalog-metadata-source").json()["requests"]
    cli_image = directory / "cli-thumbnail.webp"
    captured = await cli(environment, "catalog", "thumbnail", entry["id"], "--release", found["release"],
                         "--output", str(cli_image))
    assert captured["mediaType"] == "image/webp" and cli_image.read_bytes()[8:12] == b"WEBP"
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    async with Client(transport, mode="legacy") as mcp:
        assert await result(mcp, "catalog_metadata_search", query="试剂暂存") == found
        shown = await result(mcp, "catalog_metadata_show", id=entry["id"], release=found["release"])
        assert shown == await cli(environment, "catalog", "metadata-show", entry["id"],
                                  "--release", found["release"])
        captured = await result(mcp, "catalog_thumbnail", id=entry["id"], release=found["release"],
                                output=str(directory / "mcp-thumbnail.webp"))
        assert captured["sha256"] == "sha256:" + hashlib.sha256(cli_image.read_bytes()).hexdigest()
    assert before == http.get("/e2e/catalog-metadata-source").json()["requests"]


@pytest.mark.parametrize("metadata_gateway", [{"runtimeDelay": 5}], indirect=True)
async def test_cancel_closes_the_owned_runtime_child_and_keeps_unknown_usage(metadata_gateway):
    http, environment, calls, directory = metadata_gateway
    assert http.patch(BASE + "/collections/simready", json={"enabled": True}).status_code == 200
    created = await cli(environment, "catalog", "maintain", "simready", "--revision", REVISION)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (directory / "runtime.pid").exists():
        await asyncio.sleep(0.02)
    assert (directory / "runtime.pid").exists()
    pid = int((directory / "runtime.pid").read_text())
    assert psutil.pid_exists(pid)
    cancelled = http.post(BASE + "/runs/" + created["id"] + "/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["usage"]["modelCalls"] is None
    assert cancelled.json()["usage"]["modelTokens"] is None
    deadline = time.monotonic() + 3
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert not psutil.pid_exists(pid)
    assert len(calls.read_text().splitlines()) == 1
    assert http.get(BASE).json()["release"]["entryCount"] == 2


async def test_worker_source_crash_preserves_counters_and_resume_honors_cumulative_budget(metadata_gateway):
    http, environment, calls, _ = metadata_gateway
    assert http.patch(BASE + "/collections/simready", json={"enabled": True}).status_code == 200
    http.post("/e2e/catalog-metadata-source", json={"delay": 0.15})
    created = await cli(environment, "catalog", "maintain", "simready", "--revision", REVISION)
    path = BASE + "/runs/" + created["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = http.get(path).json()
        usage = current.get("sourceUsage", {})
        if usage.get("networkBytes", 0) > 0 and usage.get("requests", 0) >= 2:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError(current)
    assert http.post("/e2e/catalog-metadata-worker/" + created["id"] + "/crash").json()["killed"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        failed = http.get(path).json()
        if failed["status"] == "failed":
            break
        await asyncio.sleep(0.02)
    assert failed["status"] == "failed"
    assert failed["usage"]["networkBytes"] >= usage["networkBytes"] > 0
    assert failed["usage"]["modelCalls"] == 0 and not calls.exists()
    http.post("/e2e/catalog-metadata-source", json={"delay": 0})
    assert http.post(path + "/resume").status_code == 202
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        resumed = http.get(path).json()
        if resumed["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.02)
    assert resumed["status"] == "succeeded", resumed
    assert resumed["usage"]["requests"] > failed["usage"]["requests"]
    assert resumed["usage"]["networkBytes"] >= failed["usage"]["networkBytes"]
    assert resumed["usage"]["modelCalls"] == 1 and len(calls.read_text().splitlines()) == 1


async def test_source_budget_exhaustion_retains_actual_transfer_without_model_calls(metadata_gateway):
    http, _, calls, _ = metadata_gateway
    assert http.patch(BASE + "/collections/simready", json={"enabled": True}).status_code == 200
    created = http.post(
        BASE + "/runs",
        json={
            "collectionId": "simready",
            "revision": REVISION,
            "budgets": {"networkBytes": 64},
        },
    ).json()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        stopped = http.get(BASE + "/runs/" + created["id"]).json()
        if stopped["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.02)
    assert stopped["status"] == "budget_exhausted", stopped
    assert stopped["usage"]["networkBytes"] > 64 and stopped["usage"]["requests"] >= 1
    assert stopped["usage"]["modelCalls"] == 0 and not calls.exists()
    assert http.get(BASE).json()["release"] is None


async def test_complete_manifest_publishes_before_deep_budget_and_keeps_full_search(metadata_gateway):
    http, _, calls, _ = metadata_gateway
    assert http.patch(BASE + "/collections/simready", json={"enabled": True}).status_code == 200
    created = http.post(
        BASE + "/runs",
        json={
            "collectionId": "simready",
            "revision": REVISION,
            "budgets": {"requests": 1, "modelTokens": 0},
        },
    ).json()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        stopped = http.get(BASE + "/runs/" + created["id"]).json()
        if stopped["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.02)
    assert stopped["status"] == "budget_exhausted", stopped
    assert stopped["coverage"]["discovered"] == stopped["coverage"]["indexed"] == 2
    assert stopped["coverage"]["extracted"] == 0 and stopped["coverage"]["extractionPending"] == 2
    release = http.get(BASE).json()["release"]
    assert release["entryCount"] == 2
    assert release["collections"][0]["coverage"]["metadataPending"] == 2
    found = http.get("/api/v1/library/search", params={"scope": "online", "provider": "simready"}).json()
    assert found["counts"]["matching"] == 2
    assert {entry["source"]["packageId"] for entry in found["results"]} == {"beaker", "bucket"}
    assert stopped["usage"]["modelCalls"] == 0 and not calls.exists()
    requests = http.get("/e2e/catalog-metadata-source").json()["requests"]
    assert len(requests) == 1 and not any(path.endswith((".usd", ".png")) for path in requests)


async def test_native_chemlab_extracted_entries_keep_their_complete_source_digest(metadata_gateway):
    http, environment, calls, directory = metadata_gateway
    assert http.patch(BASE + "/collections/chemlab", json={"enabled": True}).status_code == 200
    budgets = directory / "budgets.json"
    budgets.write_text(json.dumps({"modelTokens": 0}), encoding="utf-8")
    run = await cli(
        environment,
        "catalog",
        "maintain",
        "chemlab",
        "--revision",
        REVISION,
        "--budgets",
        str(budgets),
        "--wait",
    )
    assert run["status"] == "budget_exhausted", run
    assert run["coverage"]["indexed"] == run["coverage"]["extracted"] == 3
    assert run["coverage"]["extractionFailed"] == run["coverage"]["extractionPending"] == 0
    assert run["extractionComplete"] and run["usage"]["modelCalls"] == 0 and not calls.exists()
    found = http.get(
        "/api/v1/library/search", params={"provider": "chemlab", "scope": "online", "q": "天平"}
    ).json()
    assert found["counts"]["matching"] == 1
    selection = directory / "publish.json"
    selection.write_text(json.dumps({"inventoryIds": [run["inventoryId"]]}), encoding="utf-8")
    published = await cli(environment, "catalog", "metadata-publish", "--document", str(selection))
    assert published == http.post(BASE + "/publish", json={"inventoryIds": [run["inventoryId"]]}).json()


@pytest.mark.parametrize("metadata_gateway", [{"runtimeDelay": 4}], indirect=True)
async def test_worker_crash_closes_owned_runtime_tree_and_preserves_unknown_usage(metadata_gateway):
    http, environment, _, directory = metadata_gateway
    http.patch(BASE + "/collections/simready", json={"enabled": True})
    run = await cli(environment, "catalog", "maintain", "simready", "--revision", REVISION)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (directory / "runtime.pid").exists():
        await asyncio.sleep(0.02)
    assert (directory / "runtime.pid").exists()
    pid = int((directory / "runtime.pid").read_text())
    assert psutil.pid_exists(pid)
    assert http.post("/e2e/catalog-metadata-worker/" + run["id"] + "/crash").json()["killed"]
    deadline = time.monotonic() + 2
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert not psutil.pid_exists(pid)
    stopped = http.get(BASE + "/runs/" + run["id"]).json()
    assert stopped["status"] == "failed" and stopped["usage"]["modelCalls"] is None
    assert stopped["usage"]["modelTokens"] is None


async def test_same_revision_repeat_budget_failure_keeps_prior_rich_fields(metadata_gateway):
    http, environment, calls, _ = metadata_gateway
    http.patch(BASE + "/collections/simready", json={"enabled": True})
    first = await cli(environment, "catalog", "maintain", "simready", "--revision", REVISION, "--wait")
    assert first["status"] == "succeeded", first
    before = http.get("/api/v1/library/search", params={"provider": "simready", "scope": "online"}).json()
    before_entries = {item["id"]: item for item in before["results"]}
    assert all(item["source"]["bytes"] > 0 for item in before_entries.values())
    http.post("/e2e/catalog-metadata-source", json={"clearLocators": True})
    created = http.post(
        BASE + "/runs",
        json={"collectionId": "simready", "revision": REVISION, "budgets": {"requests": 1, "modelTokens": 0}},
    ).json()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        stopped = http.get(BASE + "/runs/" + created["id"]).json()
        if stopped["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.02)
    assert stopped["status"] == "budget_exhausted", stopped
    after = http.get("/api/v1/library/search", params={"provider": "simready", "scope": "online"}).json()
    assert {item["id"] for item in after["results"]} == set(before_entries)
    for item in after["results"]:
        assert item["source"]["bytes"] == before_entries[item["id"]]["source"]["bytes"]
        assert item["names"] == before_entries[item["id"]]["names"]
    assert len(calls.read_text().splitlines()) == 1
