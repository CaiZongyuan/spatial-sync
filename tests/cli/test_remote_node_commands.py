"""Installed CLI and outbound runner use the real authenticated HTTP boundary."""

from apps.api.app.settings import Settings

OPERATOR = {"Authorization": "Bearer operator-fixture"}


def test_installed_cli_and_node_runner_join_and_reconnect(tmp_path, monkeypatch):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    import httpx

    from tests.gateway.test_mjlab_node_jobs import _start_server, _wait_for

    settings = Settings(remote=True, operator_token="operator-fixture")
    _, server, thread = _start_server(settings)
    root = tmp_path / "remote-runner"
    lw = Path(sys.executable).with_name("lw")
    environment = os.environ | {"LW_URL": settings.node_gateway_url, "LW_TOKEN": "operator-fixture"}
    monkeypatch.delenv("SPATIAL_NODE_TOKEN", raising=False)

    def cli(*args):
        called = subprocess.run(
            [str(lw), *args, "--json"], env=environment, capture_output=True, text=True, timeout=10
        )
        assert called.returncode == 0, called.stdout + called.stderr
        return json.loads(called.stdout)

    def join(*extra):
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "apps.node.runner",
                "--url",
                settings.node_gateway_url,
                "--root",
                str(root),
                "--node-id",
                "cpu-worker",
                "--interval",
                "0.05",
                *extra,
            ]
        )

    try:
        code = cli("node", "issue", "--node-id", "cpu-worker")["code"]
        with httpx.Client(base_url=settings.node_gateway_url, headers=OPERATOR, trust_env=False) as http:

            def registered():
                response = http.get("/api/v1/nodes/cpu-worker")
                return response.json().get("instanceId")

            previous = None
            for args in (("--registration-code", code), ()):
                runner = join(*args)
                try:
                    previous = _wait_for(registered, lambda value: value and value != previous, timeout=3)
                finally:
                    (root / "stop").parent.mkdir(parents=True, exist_ok=True)
                    (root / "stop").touch()
                    try:
                        runner.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        runner.kill()
                        runner.wait(timeout=3)
                    (root / "stop").unlink()
                assert runner.returncode == 0
            assert cli("node", "show", "cpu-worker")["instanceId"] == previous
            assert cli("node", "ls")[0]["nodeId"] == "cpu-worker"
            assert cli("node", "revoke", "cpu-worker") == {"nodeId": "cpu-worker", "revoked": True}
            assert http.get("/api/v1/nodes/cpu-worker").status_code == 404
    finally:
        server.should_exit = True
        thread.join(timeout=10)
