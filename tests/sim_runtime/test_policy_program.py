"""A numerical ONNX fixture uses the real Newton PhysicsPort; no pickPlace quality claim."""

from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.programs.robot_arm.policy import PolicyProgram
from packages.sim_runtime.info_catalog import load_info_catalog
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.simulation import NewtonSimulation
from tests.fixtures.policy import observation_spec
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN
from tests.unit.test_policy_program_contract import Invocation


def test_real_newton_port_policy_init_cpu_inference_targets_and_cancel_without_weld(tmp_path):
    with NewtonSimulation("robot-and-tubes", device="cpu") as sim:
        manifest = export_manifest(
            sim, session_id="policy-engineering", scene_revision="rev", revision_numeric_id=1
        )
        catalog = load_info_catalog(manifest, "robot-and-tubes")
        base = catalog.program_config("/World/Lab/RobotA").model_dump()
        spec = observation_spec()
        selected = base["jointKeys"][1]
        spec["observations"][0]["keys"] = [selected]
        spec["action"]["jointKeys"] = [selected]
        with TestClient(create_app(Settings(node_token=TOKEN))) as client:
            job = uploaded_job(client, tmp_path, spec=spec)
            response = client.post(
                "/api/v1/policies",
                json={
                    "checkpoint": job["jobId"] + "/checkpoints/model_0.pt",
                },
            )
            assert response.status_code == 201, response.text
            policy = response.json()
        port = PhysicsPort(sim, manifest)

        def forbidden(*args):
            raise AssertionError("Policy must never call weld/unweld")

        port.weld = port.unweld = forbidden
        config = PolicyProgram.Config(
            policy=policy["id"],
            **{
                key: base[key]
                for key in (
                    "jointKeys",
                    "fingerKeys",
                    "tubes",
                    "tcpSite",
                    "locations",
                )
            },
        )
        values = {}
        ctx = SimpleNamespace(
            physics=port,
            config=config,
            key="/World/Lab/RobotA",
            sim_time=0.0,
            fact_due=True,
            set=lambda **fields: values.update(fields),
            read=lambda key, prop: "/World/Lab/Rack/A1" if key.endswith("Tube01") else None,
            check=lambda conditions: ["not yet"] if conditions and "all" in conditions[0] else [],
        )
        program = PolicyProgram()
        program.init(ctx)
        assert program.model.session.get_providers() == ["CPUExecutionProvider"]
        assert values["jointPositions"] == pytest.approx([0.0, -0.4, 0.0, -2.0, 0.0, 1.6, 0.7])
        inv = Invocation()
        assert program.accept(inv, ctx) is None
        program.start(inv, ctx)
        program.tick(ctx, 1 / 60)
        port.commit()
        # Actual -0.4 coordinate -> scaled observation -0.8 -> bounded target -0.48.
        target = port.bind_joint(selected).target
        assert sim.control.joint_target_q.numpy()[target] == pytest.approx([-0.48], abs=1e-6)
        sim.step(8)
        port.capture()
        assert np.isfinite(port.joint_q(selected)).all()
        assert inv.outcome is None
        program.cancel(inv, ctx)
        port.commit()
        assert inv.outcome == "cancelled" and port.welds.counts()[1] == 0
