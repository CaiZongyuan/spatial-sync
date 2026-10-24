"""Program scheduling/actuator boundary with real ONNX; no physical quality claims."""

from types import SimpleNamespace

import numpy as np
import pytest

from packages.policies.inference import CpuPolicy
from packages.policies.observations import ObservationSpec
from packages.programs.robot_arm.policy import PolicyProgram
from tests.fixtures.policy import model_bytes, observation_spec
from tests.fixtures.training_task import task_document


class Port:
    def __init__(self):
        self.targets = []

    def joint_q(self, key):
        return np.array([0.1])

    def joint_qd(self, key):
        return np.array([0.0])

    def set_joint_target(self, key, values):
        self.targets.append((key, list(values)))

    def weld(self, *args):
        raise AssertionError("Contact policy must never call weld")

    unweld = weld


class Invocation:
    action = "pickPlace"
    params = {"from": "rack.A1", "to": "centrifuge.slot1"}

    def __init__(self):
        self.outcome = None

    def succeed(self):
        self.outcome = "succeeded"

    def fail(self, code, message):
        self.outcome = code

    def cancelled(self):
        self.outcome = "cancelled"


def runtime(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(model_bytes())
    program = PolicyProgram()
    program.task = task_document()
    program.spec = ObservationSpec.model_validate(observation_spec())
    program.model = CpuPolicy(path, program.spec)
    program.joints = {key: key for key in program.spec.action.jointKeys}
    program.tubes = {"/tube": 0}
    program.locations = {
        "rack.A1": {"site": "/from", "zone": "rack"},
        "centrifuge.slot1": {
            "site": "/to",
            "zone": "centrifuge",
            "requires": [{"property": "lidOpen", "equals": True}],
        },
    }
    program.handle = None
    program.previous = np.zeros(1)
    ctx = SimpleNamespace(
        physics=Port(),
        sim_time=0.0,
        fact_due=False,
        check=lambda conditions: ["not yet"] if conditions and "all" in conditions[0] else [],
        read=lambda key, prop: "/from",
        set=lambda **values: None,
        config=SimpleNamespace(maxSeconds=1.0),
    )
    return program, ctx


def test_task_frequency_raw_previous_output_bounded_targets_timeout_and_stop(tmp_path):
    program, ctx = runtime(tmp_path)
    inv = Invocation()
    assert program.accept(inv, ctx) is None
    program.start(inv, ctx)
    program.tick(ctx, 1 / 60)
    assert program.previous.tolist() == pytest.approx(
        [0.2]
    )  # raw: q.1 * observation scale2, no action scale.
    assert ctx.physics.targets[0][1] == pytest.approx([0.12])
    ctx.sim_time = 1 / 60
    program.tick(ctx, 1 / 60)
    assert len(ctx.physics.targets) == 1
    ctx.sim_time = 1 / 30
    program.tick(ctx, 1 / 60)
    assert len(ctx.physics.targets) == 2
    assert inv.outcome is None  # A numerical model does not report physical success.
    ctx.sim_time = 1.0
    program.tick(ctx, 1 / 60)
    assert inv.outcome == "motion_timeout" and program.handle is None
    program.start(inv, ctx)
    stop = Invocation()
    stop.action = "stop"
    program.start(stop, ctx)
    assert inv.outcome == "cancelled" and stop.outcome == "succeeded" and program.handle is None


def test_ambiguous_object_out_of_coverage_and_insufficient_control_frequency_reject(tmp_path):
    program, ctx = runtime(tmp_path)
    inv = Invocation()
    program.tubes["/other"] = 1
    assert program.accept(inv, ctx).code == "precondition_failed"
    del program.tubes["/other"]
    inv.params = {"from": "rack.A2", "to": "centrifuge.slot1"}
    assert program.accept(inv, ctx).code == "precondition_failed"
    inv.params = Invocation.params
    program.start(inv, ctx)
    program.tick(ctx, 1 / 15)
    assert inv.outcome == "policy_inference_failed"


@pytest.mark.parametrize("ending", ["stop", "cancel", "timeout", "success", "inference_error"])
def test_termination_keeps_the_reserved_zone_until_observed_safe_departure(tmp_path, ending):
    program, ctx = runtime(tmp_path)
    values = {}
    ctx.set = lambda **fields: values.update(fields)
    inv = Invocation()
    assert program.accept(inv, ctx) is None
    assert values["zone"] == "centrifuge"
    program.start(inv, ctx)
    if ending == "stop":
        stop = Invocation()
        stop.action = "stop"
        program.start(stop, ctx)
    elif ending == "cancel":
        program.cancel(inv, ctx)
    else:
        if ending == "timeout":
            ctx.sim_time = 1.0
        elif ending == "success":
            ctx.check = lambda _: []  # Controlled Task oracle, no physical success claim.
        else:

            def failed_inference(_):
                raise ValueError("explicit model failure")

            program.model.infer = failed_inference
        program.tick(ctx, 1 / 60)
    assert program.handle is None
    assert values["zone"] == "centrifuge"


def test_multiple_interlocked_zones_reject_without_a_permissive_reservation(tmp_path):
    program, ctx = runtime(tmp_path)
    values = {}
    ctx.set = lambda **fields: values.update(fields)
    program.locations["rack.A1"]["requires"] = [{"property": "doorOpen", "equals": True}]
    refused = program.accept(Invocation(), ctx)
    assert refused.code == "precondition_failed"
    assert "zone" not in values
