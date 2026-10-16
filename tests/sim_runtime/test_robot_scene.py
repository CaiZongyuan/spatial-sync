import numpy as np
import pytest

newton = pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.sources.newton_source import NewtonSource
from packages.protocol.schema import Command, SessionOptions
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.simulation import NewtonSimulation


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
                sim, session_id="robot", scene_revision="strict", revision_numeric_id=2, strict_labels=True,
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
        assert np.linalg.norm(moved[index, :3] - initial[index, :3]) > 1e-3
        tube_indices = [i for i, label in enumerate(sim.model.body_label) if "/Tubes/Tube" in label]
        assert np.all(moved[tube_indices, 2] < initial[tube_indices, 2] - 0.01)
        assert np.all(moved[tube_indices, 2] > 0.74)
        sim.reset()
        assert sim.sim_time == 0 and sim.epoch == 1
        np.testing.assert_allclose(sim.body_poses(), initial, atol=1e-6)
        sim.step(120)
        np.testing.assert_allclose(sim.body_poses(), moved, atol=1e-6)


def test_featherstone_precontact_robot_tracking_is_a_bounded_comparison_only():
    with NewtonSimulation("robot-and-tubes", device="cpu", solver="featherstone") as sim:
        initial = sim.body_poses()
        sim.step(30)
        poses = sim.body_poses()
        assert np.isfinite(poses).all()
        assert np.linalg.norm(poses[10, :3] - initial[10, :3]) > 1e-3
        tube_indices = [i for i, label in enumerate(sim.model.body_label) if "/Tubes/Tube" in label]
        assert np.all(poses[tube_indices, 2] > 0.9)


async def test_robot_source_exports_prismatic_metadata_and_queries_raw_joint_coordinates():
    source = NewtonSource("robot-source")
    try:
        manifest = await source.start("robot-and-tubes", SessionOptions(device="cpu"))
        assert len(source.latest_frame().poses) == 35
        assert len([j for j in manifest.joints if j.type == "prismatic"]) == 2
        assert len([j for j in manifest.joints if j.type == "free"]) == 20
        pause = await source.execute(Command(requestId="pause", command="pause"))
        assert pause.ack.status == "applied"
        reset = await source.execute(Command(requestId="reset", command="reset"))
        assert reset.frame.simTime == 0 and reset.frame.epoch == 1
        joint = next(j for j in manifest.joints if j.entityKey.endswith("/fr3_joint4"))
        finger = next(j for j in manifest.joints if j.type == "prismatic")
        assert (await source.query_joint_state(joint.entityKey)).positions == pytest.approx([-2.0])
        assert (await source.query_joint_state(finger.entityKey)).positions == pytest.approx([0.02])
        step = await source.execute(Command(requestId="step", command="step", args={"count": 60}))
        assert step.frame.simTime == pytest.approx(0.25)
        assert (await source.query_joint_state(joint.entityKey)).positions[0] != pytest.approx(-2.0, abs=1e-3)
        await source.execute(Command(requestId="turn-1", command="step", args={"count": 1000}))
        await source.execute(Command(requestId="turn-2", command="step", args={"count": 500}))
        spindle = next(j for j in manifest.joints if j.entityKey.endswith("/Spindle"))
        assert (await source.query_joint_state(spindle.entityKey)).positions[0] > np.pi
    finally:
        await source.close()
