"""Installed CLI and official MCP use the same persistent sync as public HTTP."""

import asyncio
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from cli_support import LW, ROOT, result, stop
from mcp import Client, StdioServerParameters


@pytest.fixture
def sync_gateway(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = os.environ | {
        "SPATIAL_RECORDINGS_ROOT": str(tmp_path / "recordings"),
        "SPATIAL_STATE_ROOT": str(tmp_path / "state"), "LW_LIBRARY": str(tmp_path / "library"),
        "LW_URL": f"http://127.0.0.1:{port}", "PYTHONIOENCODING": "utf-8",
    }
    with (tmp_path / "gateway.log").open("wb") as log:
        process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "catalog_sync_gateway:app", "--app-dir", "tests/fixtures",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ], cwd=ROOT, env=environment, stdout=log, stderr=log)
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
                    time.sleep(.03)
                else:
                    raise AssertionError("Owned catalog gateway did not start")
                yield http, environment
        finally:
            stop(process)


async def cli(environment, *arguments):
    process = await asyncio.create_subprocess_exec(
        str(LW), *arguments, "--json", env=environment, cwd=ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
    assert process.returncode == 0, (stdout, stderr)
    import json
    return json.loads(stdout)


async def test_sync_read_search_and_retry_through_real_cli_and_mcp(sync_gateway):
    http, environment = sync_gateway
    initial = await cli(environment, "catalog", "sync", "--provider", "simready", "--wait")
    assert initial["status"] == "succeeded" and initial["discovered"] == 2
    expected = http.get("/api/v1/library/catalog/sync/" + initial["id"]).json()
    assert await cli(environment, "catalog", "sync-status", "--id", initial["id"]) == expected
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    async with Client(transport, mode="legacy") as mcp:
        assert await result(mcp, "catalog_sync_status", id=initial["id"]) == expected
        found = await result(mcp, "catalog_search", query="CT_fixture_a01", scope="online")
        assert found["results"][0]["preview"]["kind"] == "on-demand"
        exact = await cli(environment, "catalog", "show", found["results"][0]["id"])
        assert exact["id"] == found["results"][0]["id"]
        assert not exact["acquisition"]["eligible"]
        created = await result(mcp, "catalog_sync")
        for _ in range(100):
            value = await result(mcp, "catalog_sync_status", id=created["id"])
            if value["status"] == "succeeded":
                break
            await asyncio.sleep(.02)
        assert value["status"] == "succeeded" and value["metadataReused"] == 0
        assert value["previewsReused"] == 0
        assert await result(mcp, "catalog_sync_cancel", id=value["id"]) == value
        retried = await cli(environment, "catalog", "sync-retry", value["id"])
        assert retried["retryOf"] == value["id"]
        assert (await cli(environment, "catalog", "sync-status"))["current"]["id"] == retried["id"]
