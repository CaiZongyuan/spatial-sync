"""Real CPU NewtonSimulation, HTTP API, lw CLI, MCP, and EvaluationService verification
for built-in Microduck scenes (microduck-arena@1, microduck-lab-patrol@1) and suites."""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import time
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
    response = await asyncio.wait_for(mcp.call_tool(name, arguments), 30.0)
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
        timeout=30,
    )
    assert proc.returncode == code, (arguments, proc.returncode, proc.stdout, proc.stderr)
    return json.loads(proc.stdout)


async def _run_evaluation(client: httpx.AsyncClient, suite_id: str, subject: str = "biped.posture") -> dict:
    sub = await client.post("/api/v1/evaluations", json={"subject": subject, "suite": suite_id})
    assert sub.status_code == 202, sub.text
    eval_id = sub.json()["evaluationId"]
    deadline = time.monotonic() + 120.0
    while True:
        read = await client.get(f"/api/v1/evaluations/{eval_id}")
        assert read.status_code == 200, read.text
        record = read.json()
        if record["status"] in ("completed", "failed", "cancelled"):
            assert record["status"] == "completed", record
            return record
        assert time.monotonic() < deadline, record
        await asyncio.sleep(0.05)


async def test_microduck_arena_and_lab_patrol_across_http_cli_mcp_and_evaluations(
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
            async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=60.0) as client:
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

                # 1. Verify microduck-arena@1 session, 14 joints, physicsDt=0.002 & posture actions
                arena_show = await _lw_cli("scene", "show", "microduck-arena@1", env=base_env)
                assert set(arena_show["waypoints"]) == {
                    "origin",
                    "north",
                    "south",
                    "east",
                    "west",
                    "northeast",
                    "northwest",
                    "turn_left",
                    "turn_right",
                }

                s1 = await client.post(
                    "/api/v1/sessions",
                    json={
                        "sceneVersion": "microduck-arena@1",
                        "source": "newton",
                        "realtimeFactor": 1.0,
                        "continueWithoutObservers": True,
                    },
                )
                assert s1.status_code == 201, s1.text
                s1_id = s1.json()["sessionId"]
                arena_bot = "/World/Lab/Microduck"
                arena_env = base_env | {"LW_SESSION": s1_id}
                try:
                    manifest1 = (await client.get(f"/api/v1/sessions/{s1_id}/scene")).json()
                    assert manifest1["source"]["physicsDt"] == pytest.approx(0.002)
                    assert manifest1["source"]["solver"] == "SolverMuJoCo"
                    assert len([j for j in manifest1["joints"] if j["type"] == "revolute"]) == 14

                    await asyncio.sleep(0.05)
                    vals0 = await _lw_cli("get", arena_bot, env=arena_env)
                    assert vals0["fallen"] is False
                    assert vals0["phase"] == "idle"
                    assert vals0["tiltDeg"] < 10.0
                    assert 0.10 < vals0["basePosition"][2] < 0.14
                    assert vals0["baseHeight"] == pytest.approx(vals0["basePosition"][2], abs=0.0025)
                    assert len(vals0["jointPositions"]) == 14

                    # Open-loop walkTo(north) executes and reports motion_timeout cleanly without crashing
                    walk_north = await client.post(
                        f"/api/v1/sessions/{s1_id}/actions",
                        json={
                            "requestId": "arena-walk-north",
                            "entityKey": arena_bot,
                            "action": "walkTo",
                            "params": {"waypoint": "north"},
                            "wait": True,
                            "timeoutSeconds": 10.0,
                        },
                    )
                    assert walk_north.status_code == 200, walk_north.text
                    assert walk_north.json()["status"] == "failed"
                    assert walk_north.json()["error"]["code"] == "motion_timeout"

                    # Reset before controlled fall induction via standUp(fromFall="front")
                    assert (
                        await client.post(
                            f"/api/v1/sessions/{s1_id}/commands",
                            json={"requestId": "arena-reset-1", "command": "reset"},
                        )
                    ).status_code == 200

                    # Controlled fall induction via standUp(fromFall="front") tips Microduck past maxTiltDeg
                    stand_front = await client.post(
                        f"/api/v1/sessions/{s1_id}/actions",
                        json={
                            "requestId": "arena-stand-front",
                            "entityKey": arena_bot,
                            "action": "standUp",
                            "params": {"fromFall": "front"},
                            "wait": True,
                            "timeoutSeconds": 10.0,
                        },
                    )
                    assert stand_front.status_code == 200, stand_front.text
                    assert stand_front.json()["status"] == "failed"
                    assert stand_front.json()["error"]["code"] == "motion_timeout"
                    vals_fallen = await _lw_cli("get", arena_bot, env=arena_env)
                    assert vals_fallen["fallen"] is True
                    assert vals_fallen["tiltDeg"] >= 65.0
                    assert vals_fallen["baseHeight"] <= 0.070

                    # Reset restores upright standing equilibrium; bow executes forward pitch excursion
                    rst = await client.post(
                        f"/api/v1/sessions/{s1_id}/commands",
                        json={"requestId": "arena-reset-2", "command": "reset"},
                    )
                    assert rst.status_code == 200, rst.text
                    await asyncio.sleep(0.05)
                    vals_reset = await _lw_cli("get", arena_bot, env=arena_env)
                    assert vals_reset["fallen"] is False
                    assert vals_reset["tiltDeg"] < 10.0

                    bow_res = await client.post(
                        f"/api/v1/sessions/{s1_id}/actions",
                        json={
                            "requestId": "arena-bow",
                            "entityKey": arena_bot,
                            "action": "bow",
                            "wait": True,
                            "timeoutSeconds": 10.0,
                        },
                    )
                    assert bow_res.status_code == 200, bow_res.text
                    assert bow_res.json()["status"] in ("succeeded", "failed")
                finally:
                    assert (await client.delete(f"/api/v1/sessions/{s1_id}")).status_code == 204

                # 2. Verify microduck-lab-patrol@1 multi-entity coexistence across HTTP, lw CLI & MCP
                patrol_show = await _lw_cli("scene", "show", "microduck-lab-patrol@1", env=base_env)
                expected_patrol_wps = {
                    "dock",
                    "aisle_west",
                    "bench_north",
                    "centrifuge_bay",
                    "corridor_east",
                    "inspect_turn",
                }
                assert set(patrol_show["waypoints"]) == expected_patrol_wps

                s2 = await client.post(
                    "/api/v1/sessions",
                    json={
                        "sceneVersion": "microduck-lab-patrol@1",
                        "source": "newton",
                        "realtimeFactor": 1.0,
                        "continueWithoutObservers": True,
                    },
                )
                assert s2.status_code == 201, s2.text
                s2_id = s2.json()["sessionId"]
                patrol_bot = "/World/Lab/PatrolDuck"
                centrifuge = "/World/Lab/Centrifuge"
                patrol_env = base_env | {"LW_SESSION": s2_id}
                try:
                    manifest2 = (await client.get(f"/api/v1/sessions/{s2_id}/scene")).json()
                    assert manifest2["source"]["physicsDt"] == pytest.approx(0.002)
                    # 14 PatrolDuck + 2 Centrifuge revolute joints
                    assert len([j for j in manifest2["joints"] if j["type"] == "revolute"]) == 16

                    bipeds = await _lw_cli("ls", "--type", "biped/v1", env=patrol_env)
                    assert [item["key"] for item in bipeds] == [patrol_bot]

                    async with GatewayClient(http=client, session=s2_id) as gw:
                        mcp_srv = await create_server(gw)
                        async with Client(
                            mcp_srv, client_info=Implementation(name="patrol-verifier", version="1")
                        ) as mcp:
                            assert "Waypoints:" in mcp.session.instructions
                            assert "centrifuge_bay" in mcp.session.instructions

                            mcp_desc = await _mcp_call(
                                mcp, "library_describe", id="microduck-lab-patrol@1", view="full"
                            )
                            assert set(mcp_desc["waypoints"]) == expected_patrol_wps

                            bad_wp = await _mcp_call(
                                mcp,
                                "invoke_action",
                                key=patrol_bot,
                                action="walkTo",
                                params={"waypoint": "north"},
                            )
                            assert bad_wp["status"] == "rejected"
                            assert bad_wp["error"]["code"] == "unknown_waypoint"

                            # Operate lab Centrifuge alongside standing PatrolDuck
                            opened = await _mcp_call(mcp, "invoke_action", key=centrifuge, action="openLid")
                            assert opened["status"] == "succeeded"
                            closed = await _mcp_call(mcp, "invoke_action", key=centrifuge, action="closeLid")
                            assert closed["status"] == "succeeded"

                            assert (
                                await client.post(
                                    f"/api/v1/sessions/{s2_id}/commands",
                                    json={"requestId": "patrol-reset", "command": "reset"},
                                )
                            ).status_code == 200

                            # PatrolDuck starts at dock (-0.55, 0.35, yaw=-45 deg) within arrival tolerance
                            at_dock = await _lw_cli(
                                "do", patrol_bot, "walkTo", "waypoint=dock", "--wait", env=patrol_env
                            )
                            assert at_dock["status"] == "succeeded"
                            patrol_vals = await _lw_cli("get", patrol_bot, env=patrol_env)
                            assert patrol_vals["fallen"] is False
                            assert patrol_vals["currentWaypoint"] == "dock"
                            assert patrol_vals["lastCompletedAction"] == "walkTo"
                            assert math.hypot(
                                patrol_vals["basePosition"][0] - (-0.55),
                                patrol_vals["basePosition"][1] - 0.35,
                            ) <= 0.08
                finally:
                    assert (await client.delete(f"/api/v1/sessions/{s2_id}")).status_code == 204

                # 3. Verify all 3 built-in embodied suites via public HTTP & EvaluationService execution
                for suite_slug in ("microduck-walk-to", "microduck-stand-up", "microduck-bow"):
                    pub_resp = await client.get(f"/api/v1/suites/{suite_slug}/1")
                    assert pub_resp.status_code == 200, pub_resp.text
                    pub = pub_resp.json()
                    assert pub["scene"] == "microduck-arena@1"
                    assert pub["source"] == "newton"
                    assert pub["target"] == "/World/Lab/Microduck"

                bow_eval = await _run_evaluation(client, "suite/microduck-bow@1")
                assert bow_eval["result"]["decision"] == "baseline"
                assert bow_eval["result"]["episodes"] == 4
                assert all(ep["milestones"]["bowed"] is True for ep in bow_eval["episodes"])
                assert all(ep["recordingId"].startswith("session-") for ep in bow_eval["episodes"])

                stand_eval = await _run_evaluation(client, "suite/microduck-stand-up@1")
                assert stand_eval["result"]["decision"] == "baseline"
                assert stand_eval["result"]["episodes"] == 4
                assert all(ep["milestones"]["fallen"] is True for ep in stand_eval["episodes"])

                walk_eval = await _run_evaluation(client, "suite/microduck-walk-to@1")
                assert walk_eval["result"]["decision"] == "baseline"
                assert walk_eval["result"]["episodes"] == 6
                assert walk_eval["result"]["successes"] == 0
                assert all(ep["milestones"]["walking"] is True for ep in walk_eval["episodes"])
        finally:
            server.should_exit = True
            await server_task
            sock.close()


def test_microduck_four_way_fall_induction_settles_on_floor_before_handoff(tmp_path: Path) -> None:
    import yaml

    from packages.info.catalog import DEVICE_ROOT, compile_catalog
    from packages.info.program import HostCore
    from packages.library.common import BUILTIN_ROOT
    from packages.library.scenes import SceneLibrary
    from packages.protocol.schema import Actor, InvokeRequest
    from packages.sim_runtime.info_catalog import REGISTRY
    from packages.sim_runtime.library_scene import prepare_scene_config
    from packages.sim_runtime.manifest_export import export_manifest
    from packages.sim_runtime.physics_port import PhysicsPort
    from packages.sim_runtime.simulation import NewtonSimulation

    scenes = SceneLibrary(
        builtin_root=BUILTIN_ROOT,
        user_root=tmp_path / "user",
        state_root=tmp_path / "state",
    )
    arena = scenes.resolve("microduck-arena@1", launch=True, source="newton")
    payload = arena.runtime_payload()
    sim_cfg = prepare_scene_config(payload, include_visuals=False)
    biped_type = yaml.safe_load((DEVICE_ROOT / "types/biped.v1.yaml").read_text(encoding="utf-8"))
    entity = "/World/Lab/Microduck"
    binding = dict(payload["structure"]["effectiveBindings"]["newton"][0])
    cfg = dict(binding["config"]) | {"locations": {}}
    posture_cls = REGISTRY["biped.posture"]
    catalog = compile_catalog(
        "rev-microduck-falls",
        {"biped/v1": biped_type},
        {entity: binding | {"config": cfg}},
        {entity: posture_cls.Config.model_validate(cfg)},
    )
    actor = Actor(kind="user", name="tester")

    with NewtonSimulation(
        "microduck-arena@1",
        device="cpu",
        physics_dt=sim_cfg.physics_dt,
        solver=arena.metadata["simulation"]["solver"],
        config=sim_cfg,
    ) as sim:
        manifest = export_manifest(
            sim, session_id="s-fall", scene_revision="rev-fall", revision_numeric_id=1
        )
        for direction in ("front", "back", "left", "right"):
            sim.reset()
            port = PhysicsPort(sim, manifest)
            sim_t = 0.0
            host = HostCore(
                catalog,
                REGISTRY,
                physics=port,
                source="sim",
                fact_hz=100.0,
                publication_clock=lambda: sim_t,
            )
            res = host.invoke(
                InvokeRequest(
                    invocationId=f"fall-{direction}",
                    requestId=f"req-{direction}",
                    entityKey=entity,
                    action="standUp",
                    params={"fromFall": direction},
                    actor=actor,
                    epoch=0,
                    createdSimTime=0.0,
                    createdAtMs=1.0,
                )
            )
            assert res.status in ("accepted", "running")
            port.commit()
            handoff_vals = None
            dt_ctrl = 0.02
            for step_idx in range(180):
                sim.step(10)
                port.capture()
                sim_t = round((step_idx + 1) * dt_ctrl, 6)
                host.tick(sim_t, dt_ctrl)
                port.commit()
                vals = host.values()[entity]
                if vals["fallen"] is True:
                    handoff_vals = vals
                    break
            assert handoff_vals is not None, f"Direction {direction} did not reach settled fall handoff"
            assert handoff_vals["baseHeight"] <= 0.070, (direction, handoff_vals)
            assert handoff_vals["tiltDeg"] >= 65.0, (direction, handoff_vals)
