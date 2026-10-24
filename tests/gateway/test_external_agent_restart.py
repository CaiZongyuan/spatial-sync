"""A killed owned gateway loses its in-process Source and fences old case work."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from tests.gateway.test_external_agent_evaluation import BASE, create, work

ROOT = Path(__file__).resolve().parents[2]


def test_real_gateway_process_restart_never_revives_a_grant_or_incomplete_evaluation(tmp_path):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    env = os.environ | {
        "SPATIAL_STATE_ROOT": str(tmp_path / "state"),
        "LW_LIBRARY": str(tmp_path / "library"),
        "SPATIAL_RECORDINGS_ROOT": str(tmp_path / "recordings"),
        "SPATIAL_LIVE_BINDINGS": "",
        "SPATIAL_NODE_TRAINER": "[]",
        "SPATIAL_REMOTE": "0",
    }
    log = (tmp_path / "owned-gateway.log").open("w", encoding="utf-8")
    processes = []

    def start():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "external_evaluation_gateway:app",
                "--app-dir",
                "tests/fixtures",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        for _ in range(200):
            assert process.poll() is None, "Owned fixture gateway exited before readiness"
            try:
                if http.get("/api/v1/health", timeout=0.2).is_success:
                    return process
            except httpx.HTTPError:
                pass
            time.sleep(0.025)
        raise AssertionError("Owned fixture gateway never became ready")

    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as http:
            first = start()
            run, _ = create(http)
            item = work(http, run)
            token = item["connection"]["token"]
            first.kill()  # Only this test's creating Popen handle; no external runtime exists.
            first.wait(timeout=5)
            second = start()
            recovered = http.get(BASE + run["id"]).json()
            assert recovered["status"] == "failed" and recovered["error"]["code"] == "gateway_restarted"
            assert recovered["cases"][0]["status"] == "invalid"
            assert recovered["cleanup"]["complete"]
            denied = http.get(
                "/api/v1/sessions/" + item["sessionId"] + "/state",
                headers={"Authorization": "Bearer " + token},
            )
            assert denied.status_code == 401
            stale = http.post(
                BASE + run["id"] + "/complete",
                json={
                    "caseRunId": item["caseRunId"],
                    "nativeSessionId": "late-after-process-restart",
                },
            )
            assert stale.status_code == 409
            assert http.get("/api/v1/health").json()["sessionCount"] == 0
            assert http.get("/api/v1/suites/lab-ops-basic/1/scoreboard").json()["rows"] == []
            assert second.poll() is None
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        log.close()
