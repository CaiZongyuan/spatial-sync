"""A real loopback gateway and installed entrypoint for CLI/stdio integration."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from cli_support import LW, ROOT, stop


@pytest.fixture(scope="module")
def gateway_directory(tmp_path_factory):
    return tmp_path_factory.mktemp("lw-gateway")


@pytest.fixture(scope="module")
def gateway_url(gateway_directory):
    directory = gateway_directory
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (directory / "gateway.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "apps.api.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            env=os.environ | {
                "SPATIAL_RECORDINGS_ROOT": str(directory / "recordings"),
                "SPATIAL_STATE_ROOT": str(directory / "state"),
                "LW_LIBRARY": str(directory / "user-library"),
            },
        )
        url = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(base_url=url, trust_env=False, timeout=1) as http:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    assert process.poll() is None, (directory / "gateway.log").read_text()
                    try:
                        if http.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.02)
                else:
                    raise AssertionError("Gateway did not become ready")
            assert LW.is_file(), "The project must be installed, including the lw entrypoint"
            yield url
        finally:
            stop(process)


@pytest.fixture
def lab(gateway_url):
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=10) as http:
        created = http.post("/api/v1/sessions", json={"sceneId": "lab-demo", "realtimeFactor": 30})
        created.raise_for_status()
        session = created.json()["sessionId"]
        route = "/api/v1/sessions/" + session
        environment = os.environ | {"LW_URL": gateway_url, "LW_SESSION": session, "PYTHONIOENCODING": "utf-8"}
        try:
            yield http, route, environment
        finally:
            http.delete(route)


@pytest.fixture
def lw(lab):
    def invoke(*arguments, code=0, env=None):
        result = subprocess.run(
            [str(LW), *arguments, "--json"],
            env=env or lab[2],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert result.returncode == code, (arguments, result.stdout, result.stderr)
        return json.loads(result.stdout)

    return invoke
