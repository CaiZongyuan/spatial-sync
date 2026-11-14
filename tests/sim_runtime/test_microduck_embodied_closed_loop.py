"""Multi-surface CPU NewtonSimulation closed-loop verification for built-in Microduck @2 scenes
(microduck-arena@2 and microduck-lab-patrol@2) across HTTP API, lw CLI, and MCP."""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import uvicorn
from lw.client import GatewayClient
from lw.mcp_server import create_server
from mcp import Client, Implementation

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
LW = Path(sys.executable).parent / ("lw.exe" if os.name == "nt" else "lw")


async def _mcp_call(mcp: Client, name: str, **arguments):
    response = await asyncio.wait_for(mcp.call_tool(name, arguments), 60.0)
    assert not response.is_error, response.content
    document = response.structured_content
    return document["result"] if set(document) == {"result"} else document


async def _lw_cli(*arguments: str, env: dict[str, str], code: int = 0):
    proc = await asyncio.to_thread(
        subprocess.run,
        [str(LW), *arguments, "--json"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == code, (arguments, proc.returncode, proc.stdout, proc.stderr)
    return json.loads(proc.stdout)


async def test_microduck_default_v2_scenes_closed_loop_across_http_cli_and_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert LW.is_file(), "Installed lw CLI entrypoint is required"
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    rec_root = tmp_path / "recordings"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SPATIAL_RECORDINGS_ROOT", str(rec_root))

    settings = Settings(state_root=state_root, recordings_root=rec_root)
    app = create_app(settings)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    async with app.router.lifespan_context(app):
        uv_cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
        server = uvicorn.Server(uv_cfg)
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=90.0) as client:
                for _ in range(100):
                    try:
                        if (await client.get("/api/v1/health")).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.02)
                else:
                    raise AssertionError("Loopback gateway server did not become ready")

                base_env = os.environ | {"LW_URL": base_url, "PYTHONIOENCODING": "utf-8"}

                # 1. Public RobotResource & Policy endpoints expose built-in Microduck artifacts
                emb_resp = await client.get("/api/v1/embodiments/microduck-sim")
                assert emb_resp.status_code == 200, emb_resp.text
                assert emb_resp.json()["currentController"]["version"] == 1

                mdl_resp = await client.get("/api/v1/robot-models/microduck-mjlab-ppo")
                assert mdl_resp.status_code == 200, mdl_resp.text
                assert mdl_resp.json()["quality"]["status"] == "evaluated"

                ctrl_resp = await client.get("/api/v1/controllers/microduck-biped")
                assert ctrl_resp.status_code == 200, ctrl_resp.text
                assert set(ctrl_resp.json()["current"]["policies"]) == {"walkTo", "standUp", "bow"}

                pol_list = (await client.get("/api/v1/policies")).json()["policies"]
                pol_ids = {item["id"] for item in pol_list}
                assert {
                    "policy/microduck-walk-to@1",
                    "policy/microduck-stand-up@1",
                    "policy/microduck-bow@1",
                }.issubset(pol_ids)

                # 2. Launch default versionless "microduck-arena" -> resolves to microduck-arena@2!
                s1 = await client.post(
                    "/api/v1/sessions",
                    json={
                        "sceneVersion": "microduck-arena",
                        "source": "newton",
                        "realtimeFactor": 6.0,
                        "continueWithoutObservers": True,
                    },
                )
                assert s1.status_code == 201, s1.text
                assert s1.json()["sceneVersion"] == "microduck-arena@2"
                s1_id = s1.json()["sessionId"]
                arena_bot = "/World/Lab/Microduck"
                arena_env = base_env | {"LW_SESSION": s1_id}
                try:
                    w_north = await client.post(
                        f"/api/v1/sessions/{s1_id}/actions",
                        json={
                            "requestId": "arena-v2-walk-north",
                            "entityKey": arena_bot,
                            "action": "walkTo",
                            "params": {"waypoint": "north"},
                            "wait": True,
                            "timeoutSeconds": 25.0,
                        },
                    )
                    assert w_north.status_code == 200, w_north.text
                    assert w_north.json()["status"] == "succeeded", w_north.text
                    vals_n = await _lw_cli("get", arena_bot, env=arena_env)
                    assert vals_n["currentWaypoint"] == "north"
                    assert vals_n["fallen"] is False
                    assert math.hypot(vals_n["basePosition"][0] - 0.45, vals_n["basePosition"][1]) <= 0.085

                    bow_res = await _lw_cli("do", arena_bot, "bow", "--wait", env=arena_env)
                    assert bow_res["status"] == "succeeded"
                    vals_bow = await _lw_cli("get", arena_bot, env=arena_env)
                    assert vals_bow["lastCompletedAction"] == "bow"
                    assert vals_bow["fallen"] is False

                    stand_res = await client.post(
                        f"/api/v1/sessions/{s1_id}/actions",
                        json={
                            "requestId": "arena-v2-stand-front",
                            "entityKey": arena_bot,
                            "action": "standUp",
                            "params": {"fromFall": "front"},
                            "wait": True,
                            "timeoutSeconds": 20.0,
                        },
                    )
                    assert stand_res.status_code == 200, stand_res.text
                    assert stand_res.json()["status"] == "succeeded", stand_res.text
                    vals_st = await _lw_cli("get", arena_bot, env=arena_env)
                    assert vals_st["lastCompletedAction"] == "standUp"
                    assert vals_st["fallen"] is False
                    assert vals_st["tiltDeg"] <= 20.0
                    assert vals_st["baseHeight"] >= 0.09
                finally:
                    assert (await client.delete(f"/api/v1/sessions/{s1_id}")).status_code == 204

                # 3. Launch default versionless "microduck-lab-patrol" -> resolves to microduck-lab-patrol@2!
                #    Verify cross-scene transfer (/World/Lab/PatrolDuck) + Centrifuge across HTTP, CLI, MCP!
                s2 = await client.post(
                    "/api/v1/sessions",
                    json={
                        "sceneVersion": "microduck-lab-patrol",
                        "source": "newton",
                        "realtimeFactor": 6.0,
                        "continueWithoutObservers": True,
                    },
                )
                assert s2.status_code == 201, s2.text
                assert s2.json()["sceneVersion"] == "microduck-lab-patrol@2"
                s2_id = s2.json()["sessionId"]
                patrol_bot = "/World/Lab/PatrolDuck"
                centrifuge = "/World/Lab/Centrifuge"
                patrol_env = base_env | {"LW_SESSION": s2_id}

                try:
                    emb_live = (await client.get("/api/v1/embodiments/microduck-sim")).json()
                    assert [item["sessionId"] for item in emb_live["activeSessions"]] == [s2_id]
                    assert emb_live["activeSessions"][0]["scene"] == "microduck-lab-patrol@2"
                    assert emb_live["activeSessions"][0]["controller"]["version"] == 1

                    async with GatewayClient(http=client, session=s2_id) as gw:
                        mcp_srv = await create_server(gw)
                        async with Client(
                            mcp_srv, client_info=Implementation(name="microduck-patrol-agent", version="1")
                        ) as mcp:
                            assert "centrifuge_bay" in mcp.session.instructions

                            # Multi-waypoint sequential patrol across HTTP -> CLI -> MCP -> HTTP!
                            w1 = await client.post(
                                f"/api/v1/sessions/{s2_id}/actions",
                                json={
                                    "requestId": "patrol-1-aisle-west",
                                    "entityKey": patrol_bot,
                                    "action": "walkTo",
                                    "params": {"waypoint": "aisle_west"},
                                    "wait": True,
                                    "timeoutSeconds": 25.0,
                                },
                            )
                            assert w1.status_code == 200 and w1.json()["status"] == "succeeded", w1.text

                            w2 = await _lw_cli(
                                "do", patrol_bot, "walkTo", "waypoint=bench_north", "--wait", env=patrol_env
                            )
                            assert w2["status"] == "succeeded"

                            # Operate Centrifuge concurrently/alongside PatrolDuck via MCP!
                            close_lid = await _mcp_call(
                                mcp, "invoke_action", key=centrifuge, action="closeLid"
                            )
                            assert close_lid["status"] == "succeeded"

                            w3 = await _mcp_call(
                                mcp,
                                "invoke_action",
                                key=patrol_bot,
                                action="walkTo",
                                params={"waypoint": "centrifuge_bay"},
                            )
                            assert w3["status"] == "succeeded"
                            vals_bay = await _lw_cli("get", patrol_bot, env=patrol_env)
                            assert vals_bay["currentWaypoint"] == "centrifuge_bay"
                            assert vals_bay["fallen"] is False

                            # Bow at centrifuge_bay via MCP!
                            bow_mcp = await _mcp_call(mcp, "invoke_action", key=patrol_bot, action="bow")
                            assert bow_mcp["status"] == "succeeded"

                            # Start walkTo(dock) async via CLI and cancel cleanly via MCP stop!
                            async_walk = await _lw_cli(
                                "do", patrol_bot, "walkTo", "waypoint=dock", "--no-wait", env=patrol_env
                            )
                            assert async_walk["status"] in ("accepted", "running")
                            stop_res = await _mcp_call(mcp, "invoke_action", key=patrol_bot, action="stop")
                            assert stop_res["status"] == "succeeded"
                            cancelled = await _mcp_call(
                                mcp, "get_invocation", invocationId=async_walk["invocationId"], wait=True
                            )
                            assert cancelled["status"] == "cancelled"

                            # Fall & recover from back fall on cross-scene entity via CLI!
                            rec_back = await _lw_cli(
                                "do", patrol_bot, "standUp", "fromFall=back", "--wait", env=patrol_env
                            )
                            assert rec_back["status"] == "succeeded"
                            vals_rec = await _lw_cli("get", patrol_bot, env=patrol_env)
                            assert vals_rec["fallen"] is False
                            assert vals_rec["lastCompletedAction"] == "standUp"

                            open_lid = await _mcp_call(mcp, "invoke_action", key=centrifuge, action="openLid")
                            assert open_lid["status"] == "succeeded"
                finally:
                    assert (await client.delete(f"/api/v1/sessions/{s2_id}")).status_code == 204
        finally:
            server.should_exit = True
            await server_task
            sock.close()
