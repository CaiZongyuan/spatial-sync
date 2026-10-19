"""Installed CLI and official stdio MCP acknowledge actual default-rule episodes."""

import json
import os
import subprocess
import time

import httpx
import pytest
from cli_support import LW, ROOT, result
from mcp import Client, Implementation, StdioServerParameters

KEY = "/World/Lab/BiosafetyCabinet"


@pytest.mark.parametrize("entrypoint", ["cli", "mcp"])
async def test_alert_list_and_ack_use_real_http_episode_and_peer_identity(gateway_url, entrypoint):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    with httpx.Client(base_url=gateway_url, timeout=10, trust_env=False) as http:
        created = http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2", "controlHz": 20})
        created.raise_for_status()
        sid = created.json()["sessionId"]
        route = "/api/v1/sessions/" + sid
        environment |= {"LW_URL": gateway_url, "LW_SESSION": sid, "PYTHONIOENCODING": "utf-8"}

        def cli(*args, code=0):
            response = subprocess.run(
                [str(LW), "alerts", *args, "--json"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
            )
            assert response.returncode == code, (response.stdout, response.stderr)
            return json.loads(response.stdout)

        def command(name, **args):
            response = http.post(route + "/commands", json={"requestId": name, "command": name, "args": args})
            assert response.json()["ack"]["status"] == "applied", response.text

        try:
            command("pause")
            command("reset")
            transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
            async with Client(
                transport, client_info=Implementation(name="alert-agent", version="1"), mode="legacy"
            ) as mcp:
                listed = (
                    cli("--active") if entrypoint == "cli" else await result(mcp, "alerts_list", active=True)
                )
                assert listed == []
                accepted = http.post(
                    route + "/actions",
                    json={
                        "requestId": "uv",
                        "entityKey": KEY,
                        "action": "setUv",
                        "params": {"on": True},
                    },
                )
                assert accepted.status_code == 202, accepted.text
                command("step", count=12)
                deadline = time.monotonic() + 2
                while True:
                    actual = http.get("/api/v1/alerts", params={"sessionId": sid, "active": True}).json()[
                        "alerts"
                    ]
                    if actual:
                        break
                    assert time.monotonic() < deadline
                    time.sleep(0.005)
                [episode] = actual
                assert episode["rule"] == "unsafeUv" and episode["entityKey"] == KEY
                listed = (
                    cli("--active") if entrypoint == "cli" else await result(mcp, "alerts_list", active=True)
                )
                assert listed == actual
                if entrypoint == "cli":
                    acknowledged = cli("ack", episode["alertId"], "--note", "Inspecting front window")
                else:
                    acknowledged = await result(
                        mcp, "alert_ack", alertId=episode["alertId"], note="Inspecting front window"
                    )
                assert acknowledged["state"] == "acknowledged"
                expected = (
                    {"kind": "cli", "name": "lw"}
                    if entrypoint == "cli"
                    else {"kind": "agent", "name": "alert-agent"}
                )
                assert acknowledged["actor"] == expected
                assert acknowledged["acknowledgement"]["actor"] == expected
                assert acknowledged["acknowledgement"]["note"] == "Inspecting front window"
                public = http.get(
                    "/api/v1/alerts", params={"sessionId": sid, "state": "acknowledged"}
                ).json()["alerts"]
                assert public == [acknowledged]
                if entrypoint == "cli":
                    assert cli("ack", "missing", code=1)["error"]["statusCode"] == 404
                else:
                    unknown = await mcp.call_tool("alert_ack", {"alertId": "missing"})
                    assert unknown.is_error and "404" in unknown.content[0].text
        finally:
            http.delete(route)
