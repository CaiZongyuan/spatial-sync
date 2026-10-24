"""A numerical ONNX fixture uses the real Newton PhysicsPort; no pickPlace quality claim."""

from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.pipeline import ActionPipeline
from packages.info.program import HostCore
from packages.info.store import StateStore
from packages.programs.robot_arm.newton import HOME
from packages.programs.robot_arm.policy import PolicyProgram
from packages.protocol.schema import ActionRequest, Actor, SessionOptions
from packages.sim_runtime.info_catalog import REGISTRY, load_info_catalog
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


async def test_policy_zone_interlock_uses_real_newton_facts_and_existing_action_pipeline(tmp_path):
    robot, centrifuge = "/World/Lab/RobotA", "/World/Lab/Centrifuge"
    with NewtonSimulation("robot-and-tubes", device="cpu") as sim:
        manifest = export_manifest(
            sim, session_id="policy-zone", scene_revision="zone-rev", revision_numeric_id=1
        )
        catalog = load_info_catalog(manifest, "robot-and-tubes")
        base = catalog.program_config(robot).model_dump()
        spec = observation_spec()
        selected = base["jointKeys"][1]
        spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = [selected]
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
        catalog.entities[robot] = catalog.entities[robot] | {
            "program": PolicyProgram.id,
            "config": config.model_dump(),
        }
        catalog._configs[robot] = config
        port = PhysicsPort(sim, manifest)

        def forbidden(*args):
            raise AssertionError("Contact policy must never call weld/unweld")

        port.weld = port.unweld = forbidden

        def seed_measured_joints(positions, velocity=0.0):
            # Controlled real Newton state fixture, not a claimed physical pickPlace.
            q, qd = sim.state.joint_q.numpy().copy(), sim.state.joint_qd.numpy().copy()
            for key, position in zip(config.jointKeys, positions, strict=True):
                joint = port.bind_joint(key)
                q[joint.q], qd[joint.qd] = position, velocity
            sim.state.joint_q.assign(q)
            sim.state.joint_qd.assign(qd)
            import newton

            newton.eval_fk(sim.model, sim.state.joint_q, sim.state.joint_qd, sim.state)
            port.capture()

        unknown = np.asarray(HOME) + 0.3
        seed_measured_joints(unknown)
        host = HostCore(catalog, REGISTRY, physics=port, source="sim", publication_clock=lambda: sim.sim_time)
        store = StateStore(catalog.sceneRevision)
        store.apply(host.snapshot())

        class LocalSource:
            availability = host.availability

            async def invoke(self, request):
                return host.invoke(request)

            async def cancel(self, invocation_id):
                return host.cancel(invocation_id)

        target = SimpleNamespace(
            state="RUNNING",
            session_id="policy-zone",
            source=LocalSource(),
            sim_time=0.0,
            epoch=0,
            supports_plans=False,
            options=SessionOptions(),
            approvals=SimpleNamespace(for_invocation=lambda _: None),
            plans=SimpleNamespace(invocation=lambda *_: None),
        )
        pipeline = ActionPipeline(target, catalog, store, lambda _: None)
        sequence = 0

        async def action(entity, name, params=None):
            nonlocal sequence
            sequence += 1
            return await pipeline.invoke(
                ActionRequest(
                    requestId=f"zone-{sequence}", entityKey=entity, action=name, params=params or {}
                ),
                "operator",
                Actor(kind="user", name="zone-engineering"),
            )

        def forward(events):
            for event in events:
                assert event.type != "server.error", event
                if event.type == "invocation.update":
                    pipeline.update(event)
                elif event.type in ("entity.snapshot", "entity.observations"):
                    store.apply(event)

        def tick():
            for _ in range(6):
                target.sim_time += 1 / 60
                # Advance the controlled sample clock with the real PhysicsPort sample.
                sim.sim_time = target.sim_time
                port.capture()
                forward(host.tick(target.sim_time, 1 / 60))
                port.commit()
            store.apply(host.snapshot())

        async def lid_denied(expected="centrifuge"):
            refused = await action(centrifuge, "closeLid")
            assert refused.status == "rejected" and refused.error.code == "precondition_failed"
            assert any(
                failed["condition"].get("property") == "zone" and failed["actual"] == expected
                for failed in refused.error.details["failed"]
            )

        await lid_denied("unknown")  # UNKNOWN must not become a permissive default null.
        assert "zone" not in host.values()[robot]
        assert "zone" not in store.values()[robot]
        seed_measured_joints(HOME)
        tick()
        assert store.values()[robot]["zone"] is None
        safe = await action(centrifuge, "closeLid")
        assert safe.status == "accepted"
        await pipeline.cancel(safe.invocationId, "operator")
        forward(host.drain_events())
        assert pipeline.table.get(safe.invocationId).status == "cancelled"

        pick = await action(robot, "pickPlace", {"from": "rack.A1", "to": "centrifuge.slot1"})
        assert pick.status == "accepted"
        assert store.values()[robot]["zone"] is None  # Fast-path read has not received the reservation.
        assert host.values()[robot]["zone"] == "centrifuge"
        await lid_denied()  # Authoritative intake protects even before the next control tick.
        tick()
        assert store.values()[robot]["zone"] == "centrifuge"
        await lid_denied()  # Reservation precedes the first policy target.
        seed_measured_joints(base["locations"]["locations"]["centrifuge.slot1"]["grasp"])
        tick()
        await lid_denied()
        stop = await action(robot, "stop")
        tick()
        assert pipeline.table.get(stop.invocationId).status == "succeeded"
        assert pipeline.table.get(pick.invocationId).status == "cancelled"
        await lid_denied()  # A stopped arm remains in the real centrifuge posture.
        seed_measured_joints(unknown)
        tick()
        assert store.values()[robot]["zone"] == "centrifuge"
        await lid_denied()  # Unknown transit never clears a previous guarded zone.
        seed_measured_joints(HOME, velocity=0.1)
        tick()
        await lid_denied()  # Position alone is not settled safety evidence.
        seed_measured_joints(HOME)
        tick()
        assert store.values()[robot]["zone"] is None
        assert (await action(centrifuge, "closeLid")).status == "accepted"
        assert port.welds.counts()[1] == 0
