"""Actual worker/runtime processes, installed CLI and official MCP share HTTP."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters

BASE = "/api/v1/library/catalog/maintenance"
REVISION = "a" * 40


@pytest.fixture
def metadata_gateway(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    calls = tmp_path / "runtime-calls.txt"
    profile = {
        "name": "deterministic-process-fixture",
        "model": "deterministic-stand-in",
        "command": [
            sys.executable,
            str(ROOT / "tests/fixtures/catalog_metadata_agent.py"),
            "--calls",
            str(calls),
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
        assert (
            await cli(
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
            == found
        )
        assert await result(mcp, "catalog_collections") == http.get(BASE + "/collections").json()
    # Same inputs reuse organized results without another runtime execution.
    repeated = await cli(environment, "catalog", "maintain", "simready", "--revision", REVISION, "--wait")
    assert repeated["status"] == "succeeded" and repeated["coverage"]["reused"] == 2
    assert repeated["usage"]["modelCalls"] == 0 and repeated["usage"]["modelTokens"] is None
    assert len(calls.read_text().splitlines()) == 1
    requests = http.get("/e2e/catalog-metadata-source").json()["requests"]
    assert not any(path.endswith((".usd", "normal.png", "preview.png")) for path in requests)
    assert not (directory / "state/catalog-metadata/metadata.sqlite3").parent.joinpath("runtime").exists()
