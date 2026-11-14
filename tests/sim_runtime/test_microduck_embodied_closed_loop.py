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


def test_microduck_v2_extended_idle_stability_beak_collapse_and_zero_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import warnings

    from packages.info.store import StateStore
    from packages.library.common import BUILTIN_ROOT
    from packages.library.scenes import SceneLibrary
    from packages.programs.deployment import authorize_builtin_policy_deployment
    from packages.protocol.internal import SessionOptions
    from packages.protocol.schema import Actor, EntityObservations, EntitySnapshot, InvokeRequest
    from packages.sim_runtime.info_host import NewtonInfoHost
    from packages.sim_runtime.library_scene import prepare_scene_config
    from packages.sim_runtime.manifest_export import export_manifest
    from packages.sim_runtime.simulation import NewtonSimulation

    state_root = tmp_path / "state"
    user_root = tmp_path / "library"
    state_root.mkdir(parents=True, exist_ok=True)
    user_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))
    monkeypatch.setenv("LW_LIBRARY", str(user_root))

    scenes = SceneLibrary(builtin_root=BUILTIN_ROOT, user_root=user_root, state_root=state_root)

    def _boot(scene_id: str, tag: str, *, with_permit: bool = True):
        scene = scenes.resolve(scene_id, launch=True, source="newton")
        permit = (
            authorize_builtin_policy_deployment(scene, state_root=state_root)
            if with_permit
            else None
        )
        cfg = prepare_scene_config(scene.runtime_payload(), include_visuals=False)
        sim_meta = scene.metadata.get("simulation", {})
        opts = SessionOptions(device="cpu", physicsDt=cfg.physics_dt or 0.002)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sim = NewtonSimulation(
                scene.id,
                device="cpu",
                physics_dt=opts.physicsDt,
                solver=sim_meta.get("solver"),
                config=cfg,
            )
        mujoco_warns = [
            str(w.message)
            for w in caught
            if "SolverMuJoCo" in str(w.message) or "standalone" in str(w.message)
        ]
        assert not mujoco_warns, (scene_id, mujoco_warns)

        manifest = export_manifest(
            sim,
            session_id=f"s-{tag}",
            scene_revision=f"rev-{tag}",
            revision_numeric_id=1,
            publish_hz=opts.basePublishHz,
            realtime_factor=opts.realtimeFactor,
            strict_labels=opts.strictLabels,
        )
        sim.step(sim_meta.get("warmupSteps", 1))
        sim.reset(increment_epoch=False)
        mirror = StateStore(manifest.sceneRevision, sim.epoch)

        def _on_events(evs, _m=mirror):
            for ev in evs:
                if isinstance(ev, EntitySnapshot | EntityObservations):
                    _m.apply(ev)

        info = NewtonInfoHost(
            sim,
            manifest,
            opts,
            _on_events,
            scene_package=scene,
            deployment_permit=permit,
            state_root=state_root,
        )
        mirror.apply(info.snapshot())
        return sim, info, mirror, opts

    def _run_to_terminal(sim, info, mirror, opts, robot_key: str, inv_id: str, action: str, params=None):
        info.invoke(
            InvokeRequest(
                invocationId=inv_id,
                requestId=f"req-{inv_id}",
                entityKey=robot_key,
                action=action,
                params=params or {},
                actor=Actor(kind="user", name="stability-test"),
                epoch=sim.epoch,
                createdSimTime=sim.sim_time,
                createdAtMs=0.0,
            )
        )
        cur = None
        for _ in range(500):
            sim.step(25)
            info.flush()
            snap = info.snapshot()
            mirror.apply(snap)
            cur = next((i for i in snap.invocations if i.invocationId == inv_id), None)
            if cur is not None and cur.status in ("succeeded", "failed", "rejected", "cancelled"):
                break
        assert cur is not None and cur.status == "succeeded", (inv_id, action, params, cur)

    def _assert_idle_15s(sim, info, mirror, opts, robot_key: str, label: str):
        batches = int(round(15.0 / (25 * opts.physicsDt)))
        for _ in range(batches):
            sim.step(25)
            info.flush()
            mirror.apply(info.snapshot())
            vals = mirror.values()[robot_key]
            assert vals["fallen"] is False, (label, vals)
            assert vals["tiltDeg"] <= 15.0, (label, vals)
            assert vals["baseHeight"] >= 0.095, (label, vals)

    # 1. Extended 15s post-walkTo, post-bow, mid-stride stop, post-standUp, and beak-propped collapse recovery
    import newton

    sim_a, info_a, mirror_a, opts_a = _boot("microduck-arena@2", "arena-extended")
    try:
        bot_a = "/World/Lab/Microduck"
        _run_to_terminal(sim_a, info_a, mirror_a, opts_a, bot_a, "w1", "walkTo", {"waypoint": "north"})
        _assert_idle_15s(sim_a, info_a, mirror_a, opts_a, bot_a, "arena:post-walkTo")

        _run_to_terminal(sim_a, info_a, mirror_a, opts_a, bot_a, "b1", "bow")
        _assert_idle_15s(sim_a, info_a, mirror_a, opts_a, bot_a, "arena:post-bow")

        info_a.invoke(
            InvokeRequest(
                invocationId="w-mid",
                requestId="req-w-mid",
                entityKey=bot_a,
                action="walkTo",
                params={"waypoint": "south"},
                actor=Actor(kind="user", name="stability-test"),
                epoch=sim_a.epoch,
                createdSimTime=sim_a.sim_time,
                createdAtMs=0.0,
            )
        )
        sim_a.step(300)  # 0.60s mid-stride
        info_a.flush()
        _run_to_terminal(sim_a, info_a, mirror_a, opts_a, bot_a, "stop-mid", "stop")
        _assert_idle_15s(sim_a, info_a, mirror_a, opts_a, bot_a, "arena:post-stop")

        _run_to_terminal(
            sim_a, info_a, mirror_a, opts_a, bot_a, "su-front", "standUp", {"fromFall": "front"}
        )
        _assert_idle_15s(sim_a, info_a, mirror_a, opts_a, bot_a, "arena:post-standUp(front)")

        # Beak-propped low-height collapse (z=0.065m, tilt=49 < 55 deg) -> fallen -> standUp() -> 15s idle
        q_np = sim_a.state.joint_q.numpy().copy()
        qd_np = sim_a.state.joint_qd.numpy().copy()
        qd_np[:] = 0.0
        half = math.radians(49.0) * 0.5
        q_np[2] = 0.065
        q_np[3], q_np[4], q_np[5], q_np[6] = 0.0, math.sin(half), 0.0, math.cos(half)
        sim_a.state.joint_q.assign(q_np)
        sim_a.state.joint_qd.assign(qd_np)
        sim_a.next_state.joint_q.assign(q_np)
        sim_a.next_state.joint_qd.assign(qd_np)
        newton.eval_fk(sim_a.model, sim_a.state.joint_q, sim_a.state.joint_qd, sim_a.state)
        newton.eval_fk(sim_a.model, sim_a.next_state.joint_q, sim_a.next_state.joint_qd, sim_a.next_state)
        sim_a.step(15)
        info_a.flush()
        mirror_a.apply(info_a.snapshot())
        vals_c = mirror_a.values()[bot_a]
        assert vals_c["fallen"] is True and vals_c["phase"] == "fallen", vals_c
        assert vals_c["tiltDeg"] < 55.0, vals_c

        _run_to_terminal(sim_a, info_a, mirror_a, opts_a, bot_a, "su-beak", "standUp")
        _assert_idle_15s(sim_a, info_a, mirror_a, opts_a, bot_a, "arena:beak-collapse-post-standUp")
    finally:
        info_a.close()

    # 2. Cross-scene patrol extended 15s stability (including standUp(fromFall="back") + zero warnings on @1)
    _sim_p1, info_p1, _, _ = _boot(
        "microduck-lab-patrol@1", "patrol-v1-warn-check", with_permit=False
    )
    info_p1.close()

    sim_p, info_p, mirror_p, opts_p = _boot("microduck-lab-patrol@2", "patrol-extended")
    try:
        bot_p = "/World/Lab/PatrolDuck"
        _run_to_terminal(
            sim_p, info_p, mirror_p, opts_p, bot_p, "pw1", "walkTo", {"waypoint": "aisle_west"}
        )
        _assert_idle_15s(sim_p, info_p, mirror_p, opts_p, bot_p, "patrol:post-walkTo")

        _run_to_terminal(
            sim_p, info_p, mirror_p, opts_p, bot_p, "psu-back", "standUp", {"fromFall": "back"}
        )
        _assert_idle_15s(sim_p, info_p, mirror_p, opts_p, bot_p, "patrol:post-standUp(back)")
    finally:
        info_p.close()
