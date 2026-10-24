"""Acquisition start/list/show/cancel/retry over HTTP, installed lw and MCP stdio.

The fixture gateway runs the real acquisition service with a deterministic
runner that copies ``tests/fixtures/acquisition/package``. It never uses the
worker subprocess or the network, and lets a test set a per-phase delay so that
progress and cancellation are observable.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from cli_support import LW, ROOT, result, stop
from mcp import Client, StdioServerParameters

REVISION = "524747d9b5e1f21b5b18b56041788c01d745f1a1"
BOX = f"simready/box_cardboard_a01@{REVISION}"
CART = f"simready/equipment_utility_cart_e01@{REVISION}"
UNKNOWN = "acq-" + "0" * 32
SUCCEEDED = "environment/cardboard-box@1"


def launch(directory, *, budget=False):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = os.environ | {
        "SPATIAL_RECORDINGS_ROOT": str(directory / "recordings"),
        "SPATIAL_STATE_ROOT": str(directory / "state"),
        "LW_LIBRARY": str(directory / "user-library"),
    }
    if budget:
        environment["LW_ACQUISITION_FIXTURE_BUDGET"] = "1"
    log = (directory / ("budget-gateway.log" if budget else "gateway.log")).open("wb")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "acquisition_gateway:app",
            "--app-dir", "tests/fixtures", "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=ROOT, stdout=log, stderr=log, env=environment,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=url, trust_env=False, timeout=1) as http:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, (directory / "gateway.log").read_text()
                try:
                    if http.get("/api/v1/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.02)
            else:
                raise AssertionError("Acquisition fixture gateway did not become ready")
    except BaseException:
        stop(process)
        raise
    return url, process, log


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    url, process, log = launch(tmp_path_factory.mktemp("acquisition-gateway"))
    try:
        yield url
    finally:
        stop(process)
        log.close()


@pytest.fixture(scope="module")
def budget_gateway(tmp_path_factory):
    url, process, log = launch(tmp_path_factory.mktemp("acquisition-budget"), budget=True)
    try:
        yield url
    finally:
        stop(process)
        log.close()


def environment_for(gateway_url):
    return {key: value for key, value in os.environ.items() if key != "LW_SESSION"} | {
        "LW_URL": gateway_url,
        "PYTHONIOENCODING": "utf-8",
    }


def delay(http, seconds):
    response = http.post("/e2e/acquisition-delay", json={"delaySeconds": seconds})
    assert response.status_code == 200, response.text


def wait_for(http, identifier, statuses=("succeeded", "failed", "cancelled"), timeout=20):
    deadline = time.monotonic() + timeout
    document = None
    while time.monotonic() < deadline:
        document = http.get(f"/api/v1/library/acquisitions/{identifier}").json()
        if document.get("status") in statuses:
            return document
        time.sleep(0.02)
    raise AssertionError(f"Acquisition did not reach {statuses}: {document}")


def record(http, identifier):
    response = http.get(f"/api/v1/library/acquisitions/{identifier}")
    assert response.status_code == 200, response.text
    return response.json()


def cli(environment, *arguments, code=0):
    invocation = subprocess.run(
        [str(LW), *arguments, "--json"],
        cwd=ROOT, env=environment, capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    expected = code if isinstance(code, tuple) else (code,)
    assert invocation.returncode in expected, (arguments, invocation.stdout, invocation.stderr)
    return json.loads(invocation.stdout)


async def cli_async(environment, *arguments, code=0):
    return await asyncio.to_thread(cli, environment, *arguments, code=code)


def error_code(document):
    return document["error"]["detail"]["code"]


def mcp_error(document):
    text = document.content[0].text
    return json.loads(text[text.index("{"):])


async def test_start_show_list_result_and_http_cli_mcp_equality(gateway):
    environment = environment_for(gateway)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    with httpx.Client(base_url=gateway, trust_env=False, timeout=30) as http:
        delay(http, 0.02)
        started = http.post("/api/v1/library/acquisitions", json={"ref": BOX})
        assert started.status_code == 202, started.text
        first = wait_for(http, started.json()["id"])
        assert first["status"] == "succeeded", first
        assert first["result"]["assetVersion"] == SUCCEEDED
        assert first["result"]["validation"]["status"] in ("launchable", "gaps")
        assert first["usage"]["sourceBytes"] == first["progress"]["totalBytes"]
        assert first["usage"]["adaptedBytes"] and first["usage"]["adaptedBytes"] > 0

        async with Client(transport, mode="legacy") as mcp:
            cli_started = await cli_async(environment, "acquisition", "start", BOX, "--wait")
            assert cli_started["status"] == "succeeded"
            for field in ("assetVersion", "contentHash", "validation", "usage"):
                assert cli_started["result"][field] == first["result"][field]
            assert cli_started["result"]["deduplicated"] is True
            assert await result(mcp, "acquisition_show", id=cli_started["id"]) == record(
                http, cli_started["id"]
            )
            assert await cli_async(
                environment, "acquisition", "show", cli_started["id"]
            ) == record(http, cli_started["id"])

            mcp_started = await result(mcp, "acquisition_start", ref=BOX, wait=True)
            assert mcp_started["status"] == "succeeded"
            assert mcp_started["result"]["assetVersion"] == SUCCEEDED
            assert mcp_started == record(http, mcp_started["id"]) == await cli_async(
                environment, "acquisition", "show", mcp_started["id"]
            )

            listing = http.get("/api/v1/library/acquisitions").json()
            assert {item["id"] for item in listing["acquisitions"]} == {
                mcp_started["id"], cli_started["id"], first["id"],
            }
            timestamps = [item["createdAt"] for item in listing["acquisitions"]]
            assert timestamps == sorted(timestamps, reverse=True)
            assert await result(mcp, "acquisition_list") == listing
            assert await cli_async(environment, "acquisition", "ls") == listing
            succeeded = http.get(
                "/api/v1/library/acquisitions", params={"status": "succeeded"}
            ).json()
            assert succeeded == listing
            assert await result(mcp, "acquisition_list", status="succeeded") == succeeded
            assert await cli_async(
                environment, "acquisition", "ls", "--status", "succeeded"
            ) == succeeded
            empty = http.get(
                "/api/v1/library/acquisitions", params={"status": "failed"}
            ).json()
            assert empty == {"acquisitions": []}
            assert await result(mcp, "acquisition_list", status="failed") == empty

            # The record result names the exact local version and never an import or usage claim.
            detail = http.get("/api/v1/library/assets/environment/cardboard-box/1")
            assert detail.status_code == 200
            assert detail.json()["contentHash"] == first["result"]["contentHash"]
            for url in detail.json()["files"].values():
                assert http.get(url).status_code == 200


async def test_progress_cancel_retry_and_bounded_wait(gateway):
    environment = environment_for(gateway)
    with httpx.Client(base_url=gateway, trust_env=False, timeout=30) as http:
        roots = http.get("/e2e/storage-roots").json()
        asset_root = Path(roots["assetLibrary"]) / "assets/environment/cardboard-box"
        baseline_revisions = sorted(asset_root.iterdir()) if asset_root.is_dir() else []
        delay(http, 5)
        started = cli(environment, "acquisition", "start", BOX)
        assert started["status"] == "queued" and started["attempt"] == 1
        deadline = time.monotonic() + 5
        observed = None
        while time.monotonic() < deadline:
            observed = cli(environment, "acquisition", "show", started["id"])
            if observed["status"] in ("downloading", "preparing"):
                break
            time.sleep(0.05)
        assert observed["status"] in ("downloading", "preparing"), observed
        assert observed["progress"]["totalBytes"] == observed["estimate"]["source"]["bytes"]
        assert observed["progress"]["completedBytes"] > 0
        assert observed["progress"]["totalFiles"] == observed["estimate"]["source"]["files"]

        cancelled = cli(environment, "acquisition", "cancel", started["id"], code=(0, 3))
        final = wait_for(http, started["id"], statuses=("cancelled",))
        assert final["cancelRequested"] is True and final["error"] is None
        assert cancelled["id"] == started["id"]
        assert cli(environment, "acquisition", "show", started["id"], code=3) == final
        roots = http.get("/e2e/storage-roots").json()
        asset_root = Path(roots["assetLibrary"]) / "assets/environment/cardboard-box"
        revisions = sorted(asset_root.iterdir()) if asset_root.is_dir() else []
        assert revisions == baseline_revisions

        delay(http, 0.02)
        retried = cli(environment, "acquisition", "retry", started["id"])
        assert retried["id"] == started["id"] and retried["attempt"] == 2
        assert retried["status"] in ("queued", "downloading", "preparing")
        completed = wait_for(http, started["id"])
        assert completed["status"] == "succeeded" and completed["attempt"] == 2
        assert cli(environment, "acquisition", "show", started["id"]) == completed

        waited = cli(environment, "acquisition", "start", BOX, "--wait")
        assert waited["status"] == "succeeded"
        assert waited == record(http, waited["id"])

        retried_again = cli(environment, "acquisition", "retry", started["id"], code=1)
        assert retried_again["error"]["statusCode"] == 422
        assert error_code(retried_again) == "invalid_acquisition"

        transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
        async with Client(transport, mode="legacy") as mcp:
            delay(http, 5)
            mcp_started = await result(mcp, "acquisition_start", ref="catalog:" + BOX)
            mcp_cancelled = await result(mcp, "acquisition_cancel", id=mcp_started["id"])
            assert mcp_cancelled["id"] == mcp_started["id"]
            mcp_final = wait_for(http, mcp_started["id"], statuses=("cancelled",))
            assert await result(mcp, "acquisition_show", id=mcp_started["id"]) == mcp_final
            delay(http, 0.02)
            mcp_retried = await result(mcp, "acquisition_retry", id=mcp_started["id"])
            assert mcp_retried["attempt"] == 2 and mcp_retried["id"] == mcp_started["id"]
            mcp_succeeded = wait_for(http, mcp_started["id"])
            assert mcp_succeeded["status"] == "succeeded"
            assert await result(mcp, "acquisition_show", id=mcp_started["id"]) == mcp_succeeded


async def test_structured_errors_unknown_ineligible_and_budget(gateway, budget_gateway):
    environment = environment_for(gateway)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    with httpx.Client(base_url=gateway, trust_env=False, timeout=30) as http:
        delay(http, 0.02)
        missing = http.get(f"/api/v1/library/acquisitions/{UNKNOWN}")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "unknown_acquisition"
        cli_missing = cli(environment, "acquisition", "show", UNKNOWN, code=1)
        assert cli_missing["error"]["statusCode"] == 404
        assert error_code(cli_missing) == "unknown_acquisition"

        ineligible = http.post("/api/v1/library/acquisitions", json={"ref": CART})
        assert ineligible.status_code == 409, ineligible.text
        detail = ineligible.json()["detail"]
        assert detail["code"] == "acquisition_ineligible"
        assert detail["reasons"] and detail["estimate"] and detail["limits"]
        assert not detail["estimate"]["acquisition"]["eligible"]
        cli_ineligible = cli(environment, "acquisition", "start", CART, code=1)
        assert cli_ineligible["error"]["statusCode"] == 409
        assert error_code(cli_ineligible) == "acquisition_ineligible"

        for ref in (
            "https://huggingface.co/datasets/nvidia/simready-assets",
            "C:\\assets\\box",
            "simready/box_cardboard_a01",
        ):
            rejected = cli(environment, "acquisition", "start", ref, code=1)
            assert rejected["error"]["statusCode"] == 422
            assert error_code(rejected) == "invalid_acquisition"

        async with Client(transport, mode="legacy") as mcp:
            mcp_missing = await mcp.call_tool("acquisition_show", {"id": UNKNOWN})
            assert mcp_missing.is_error
            assert error_code(mcp_error(mcp_missing)) == "unknown_acquisition"
            mcp_ineligible = await mcp.call_tool("acquisition_start", {"ref": CART})
            assert mcp_ineligible.is_error
            document = mcp_error(mcp_ineligible)
            assert document["error"]["statusCode"] == 409
            assert error_code(document) == "acquisition_ineligible"

    budget_environment = environment_for(budget_gateway)
    with httpx.Client(base_url=budget_gateway, trust_env=False, timeout=30) as http:
        delay(http, 0.02)
        exceeded = http.post("/api/v1/library/acquisitions", json={"ref": BOX})
        assert exceeded.status_code == 409, exceeded.text
        detail = exceeded.json()["detail"]
        assert detail["code"] == "budget_exceeded"
        assert detail["limits"]["batchBytes"] == 1
        assert detail["reasons"] and detail["estimate"]["withinLimits"] is False
        assert http.get("/api/v1/library/acquisitions").json() == {"acquisitions": []}
        cli_exceeded = cli(budget_environment, "acquisition", "start", BOX, code=1)
        assert cli_exceeded["error"]["statusCode"] == 409
        assert error_code(cli_exceeded) == "budget_exceeded"
