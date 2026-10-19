import asyncio
import time

import numpy as np
import pytest

newton = pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.sources.newton_source import NewtonSource
from packages.protocol.schema import Command, InvokeRequest, SessionOptions
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.simulation import NewtonSimulation


def test_pure_structure_uses_actual_builder_resource_keys_and_urdf_limits():
    import warp as wp

    from packages.protocol.math import normalize_entity_key
    from packages.sim_runtime.robot_definition import structure
    from packages.sim_runtime.robot_scene import build_robot_and_tubes
    from packages.sim_runtime.sites import add_joint_anchor_sites

    with wp.ScopedDevice("cpu"):
        builder = build_robot_and_tubes()
        add_joint_anchor_sites(builder)
    projected = structure()
    assert set(projected["bodies"]) == {normalize_entity_key(label) for label in builder.body_label}
    assert set(projected["joints"]) == {normalize_entity_key(label) for label in builder.joint_label}
    assert set(projected["sites"]) == {
        normalize_entity_key(label)
        for label, flags in zip(builder.shape_label, builder.shape_flags, strict=True)
        if flags & int(newton.ShapeFlags.SITE)
    }
    for index, label in enumerate(builder.joint_label):
        limits = projected["joints"][normalize_entity_key(label)]["limits"]
        if limits is not None:
            start = builder.joint_qd_start[index]
            assert limits == pytest.approx(
                [builder.joint_limit_lower[start], builder.joint_limit_upper[start]]
            )


def test_official_seven_dof_arm_initial_tcp_and_dynamic_tubes_are_authoritative():
    with NewtonSimulation("robot-and-tubes", device="cpu") as sim:
        assert type(sim.solver).__name__ == "SolverMuJoCo"
        labels = sim.model.joint_label
        arm = [
            label for label in labels if label.rsplit("/", 1)[-1] in {f"fr3_joint{i}" for i in range(1, 8)}
        ]
        assert len(arm) == 7
        assert len([label for label in sim.model.body_label if "/Tubes/Tube" in label]) == 20
        manifest = export_manifest(sim, session_id="robot", scene_revision="rev", revision_numeric_id=1)
        assert len([w for w in manifest.warnings if w.code == "unlabeled_entity"]) == 54
        with pytest.raises(ValueError, match="Missing label"):
            export_manifest(
                sim,
                session_id="robot",
                scene_revision="strict",
                revision_numeric_id=2,
                strict_labels=True,
            )
        tcp = next(site for site in manifest.sites if site.entityKey.endswith("/fr3_hand/tcp"))
        assert tcp.localPose.p == pytest.approx((0, 0, 0.1034))
        index = sim.model.body_label.index(tcp.parentBodyKey.lstrip("/"))
        body = sim.body_poses()[index]
        import warp as wp

        world = wp.transform_multiply(
            wp.transform(wp.vec3(*body[:3]), wp.quat(*body[3:])),
            wp.transform(wp.vec3(*tcp.localPose.p), wp.quat(*tcp.localPose.q)),
        )
        # Independently worked from upstream URDF origin RPY matrices and home joint values.
        assert tuple(wp.transform_get_translation(world)) == pytest.approx(
            (-0.07282344, 0, 1.26703449),
            abs=2e-6,
        )
        initial = sim.body_poses()
        sim.step(120)
        moved = sim.body_poses()
        assert np.isfinite(moved).all()
        # No action was declared: gravity settling must retain the home pose.
        assert np.linalg.norm(moved[index, :3] - initial[index, :3]) < 0.01
        arm_indices = [
            i
            for i, label in enumerate(labels)
            if label.rsplit("/", 1)[-1] in {f"fr3_joint{j}" for j in range(1, 8)}
        ]
        coordinates = sim.state.joint_q.numpy()
        starts = sim.model.joint_q_start.numpy()
        np.testing.assert_allclose(coordinates[starts[arm_indices]], [0, -0.4, 0, -2, 0, 1.6, 0.7], atol=0.01)
        tube_indices = [i for i, label in enumerate(sim.model.body_label) if "/Tubes/Tube" in label]
        # Four loaded tubes remain physically seated at bottom Sites; the rest
        # settle on the table. The body origin is not the cylinder centre.
        np.testing.assert_allclose(moved[tube_indices[:4], 2], [0.84] * 4, atol=0.003)
        assert np.all(moved[tube_indices[4:], 2] < initial[tube_indices[4:], 2] - 0.01)
        assert np.all(moved[tube_indices, 2] > 0.74)
        sim.reset()
        assert sim.sim_time == 0 and sim.epoch == 1
        np.testing.assert_allclose(sim.body_poses(), initial, atol=1e-6)
        sim.step(120)
        np.testing.assert_allclose(sim.body_poses(), moved, atol=1e-6)


def test_unsupported_featherstone_contact_reference_never_reports_performance_samples():
    from tools.check_robot_solvers import check

    # The loaded tubes already touch the rack at time zero. This is not a
    # precontact reference; a divergent run must remain an honest diagnostic.
    result = check("featherstone", "cpu")
    assert not result["reference"]["supported"]
    assert "Unsupported" in result["reference"]["diagnostic"]
    assert result["samplesMs"] == [] and result["medianMs"] is None
    assert not result["fullContactJourney"]["supportedAtDefaultDt"]


async def test_robot_source_exports_prismatic_metadata_and_queries_raw_joint_coordinates():
    source = NewtonSource("robot-source")
    try:
        manifest, catalog = await source.start("robot-and-tubes", SessionOptions(device="cpu"))
        assert len(catalog.entities) == 22
        assert len(source.latest_frame().poses) == 40
        assert len([j for j in manifest.joints if j.type == "prismatic"]) == 2
        assert len([j for j in manifest.joints if j.type == "free"]) == 20
        pause = await source.execute(Command(requestId="pause", command="pause"))
        assert pause.ack.status == "applied"
        reset = await source.execute(Command(requestId="reset", command="reset"))
        assert reset.frame.simTime == 0 and reset.frame.epoch == 1
        cached = source.entity_snapshot()
        assert cached.epoch == 1 and cached.simTime == 0
        assert (
            next(e for e in cached.entities if e.key.endswith("/Tube01")).values["location"]
            == "/World/Lab/Rack/A1"
        )
        joint = next(j for j in manifest.joints if j.entityKey.endswith("/fr3_joint4"))
        finger = next(j for j in manifest.joints if j.type == "prismatic")
        assert (await source.query_joint_state(joint.entityKey)).positions == pytest.approx([-2.0])
        assert (await source.query_joint_state(finger.entityKey)).positions == pytest.approx([0.04])

        invocation_index = 0

        async def invoke(key, action, params=None):
            nonlocal invocation_index
            invocation_index += 1
            frame = source.latest_frame()
            identity = f"{action}-{invocation_index}"
            return await source.invoke(
                InvokeRequest(
                    invocationId=identity,
                    requestId=identity,
                    entityKey=key,
                    action=action,
                    params=params or {},
                    epoch=frame.epoch,
                    createdSimTime=frame.simTime,
                    createdAtMs=time.monotonic() * 1000,
                )
            )

        moved = [0, -0.4, 0, -1.8, 0, 1.6, 0.7]
        queued = await invoke("/World/Lab/RobotA", "moveJoints", {"positions": moved})
        assert (
            next(
                i for i in source.entity_snapshot().invocations if i.invocationId == queued.invocationId
            ).status
            == "accepted"
        )
        await asyncio.sleep(0.03)
        assert source.latest_frame().simTime == 0
        assert (await source.query_joint_state(joint.entityKey)).positions == pytest.approx([-2.0])
        assert (await source.cancel(queued.invocationId)).status == "cancelled"
        revocable = await invoke("/World/Lab/RobotA", "moveJoints", {"positions": moved})
        revoked = await source.revoke_before_start(revocable.invocationId)
        assert revoked.status == "cancelled" and revoked.startedSimTime is None
        accepted = await invoke("/World/Lab/RobotA", "moveJoints", {"positions": moved})
        assert accepted.status == "accepted"
        await source.execute(Command(requestId="begin", command="step", args={"count": 4}))
        running = await source.revoke_before_start(accepted.invocationId)
        assert running.status == "running" and running.startedSimTime is not None
        step = await source.execute(Command(requestId="step", command="step", args={"count": 236}))
        assert step.frame.simTime == pytest.approx(1)
        assert (await source.query_joint_state(joint.entityKey)).positions == pytest.approx([-1.8], abs=0.01)
        assert (await invoke("/World/Lab/Centrifuge", "closeLid")).status == "accepted"
        await source.execute(Command(requestId="lid", command="step", args={"count": 240}))
        assert (
            await invoke("/World/Lab/Centrifuge", "start", {"rpm": 500, "durationSeconds": 1})
        ).status == "accepted"
        await source.execute(Command(requestId="turn-1", command="step", args={"count": 1000}))
        await source.execute(Command(requestId="turn-2", command="step", args={"count": 500}))
        spindle = next(j for j in manifest.joints if j.entityKey.endswith("/Spindle"))
        assert (await source.query_joint_state(spindle.entityKey)).positions[0] > np.pi
        pending = await invoke("/World/Lab/RobotA", "home")
        assert pending.status == "accepted"
        await source.execute(Command(requestId="reset-live", command="reset"))
        cached = source.entity_snapshot()  # Fresh before any subsequent await.
        assert cached.epoch == 2 and cached.simTime == 0
        failed = next(i for i in cached.invocations if i.invocationId == pending.invocationId)
        assert failed.status == "failed" and failed.error.code == "session_reset"
        robot = next(e for e in cached.entities if e.key == "/World/Lab/RobotA")
        assert robot.values["jointPositions"] == pytest.approx([0, -0.4, 0, -2, 0, 1.6, 0.7])
    finally:
        await source.close()
