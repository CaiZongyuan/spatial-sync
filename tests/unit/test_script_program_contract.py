"""Fixed physical facts/interlocks around a controlled delegate, without engine or uploaded Python."""

from types import SimpleNamespace

import numpy as np
import pytest

from packages.info.program import TERMINAL, ProgramConfig
from packages.programs.loading import PreparedProgram
from packages.programs.robot_arm.script import ScriptProgram


class Port:
    def __init__(self):
        self.targets = []

    def bind_joint(self, key):
        return key

    bind_site = bind_joint

    def joint_q(self, key):
        return np.array([0.1])

    def joint_qd(self, key):
        return np.array([0.0])

    def site_pose(self, key):
        return np.zeros(3), np.array([0, 0, 0, 1])

    def set_joint_target(self, key, values):
        self.targets.append((key, list(values)))


class Delegate:
    def init(self, ctx):
        pass

    def accept(self, inv, ctx):
        return None

    def start(self, inv, ctx):
        self.inv = inv

    def tick(self, ctx, dt):
        self.inv.succeed()

    def cancel(self, inv, ctx):
        inv.cancelled()


class Invocation:
    action = "pickPlace"
    params = {"from": "rack.A1", "to": "centrifuge.slot1"}

    def __init__(self):
        self.invocation = SimpleNamespace(status="running")

    def _finish(self, status):
        if self.invocation.status not in TERMINAL:
            self.invocation.status = status

    def succeed(self):
        self._finish("succeeded")

    def cancelled(self):
        self._finish("cancelled")

    def fail(self, *_):
        self._finish("failed")


def inputs(*, initialize=True):
    # Deliberate metadata-only fixture; it does not grant a Runner loading capability.
    program = object.__new__(ScriptProgram)
    record = {
        "id": "program/robot_arm.script.fixture@1",
        "contentHash": "a" * 64,
        "codeHash": "b" * 64,
        "configHash": "c" * 64,
        "archiveSha256": "d" * 64,
        "metadata": {"config": {}, "implements": "robot-arm/v1.pickPlace"},
    }
    prepared = PreparedProgram(record, Delegate(), ProgramConfig())
    program.prepared = {"/World/Lab/RobotA": prepared}
    locations = {
        "rack.A1": {"site": "/from", "zone": "rack"},
        "centrifuge.slot1": {
            "site": "/to",
            "zone": "centrifuge",
            "requires": [{"property": "lidOpen", "equals": True}],
        },
    }
    config = ScriptProgram.Config(
        jointKeys=[f"/joint{i}" for i in range(7)],
        fingerKeys=["/finger1", "/finger2"],
        tubes=[],
        tcpSite="/tcp",
        locations={"locations": locations},
        maxSeconds=1.0,
        programArtifact={"id": record["id"], "contentHash": record["contentHash"]},
        parameters={},
    )
    values = {"zone": "previous-reservation"}
    ctx = SimpleNamespace(
        config=config,
        key="/World/Lab/RobotA",
        physics=Port(),
        sim_time=0.0,
        fact_due=True,
        set=lambda **fields: values.update(fields),
        check=lambda _: [],
    )
    if initialize:
        program.init(ctx)
        assert prepared.initialized
    ctx.physics.targets.clear()
    return program, ctx, values


def test_static_script_definition_cannot_construct_in_gateway_or_live_context():
    with pytest.raises(ValueError, match="Runner"):
        ScriptProgram()


def test_failed_delegate_initialization_leaves_readiness_unconfirmed():
    program, ctx, _ = inputs(initialize=False)
    prepared = program.prepared[ctx.key]

    def failed(*_):
        raise RuntimeError("Controlled initialization failure")

    prepared.delegate.init = failed
    with pytest.raises(RuntimeError):
        program.init(ctx)
    assert not prepared.initialized


def test_idle_callbacks_do_not_drive_an_unadmitted_controller():
    program, ctx, _ = inputs()
    program.tick(ctx, 1 / 60)
    assert program.execution_evidence()["callbacks"]["tick"] == 0


@pytest.mark.parametrize("ending", ["success", "cancel", "stop", "timeout", "exception"])
def test_script_termination_holds_actuators_and_preserves_unknown_feedback_zone(ending):
    program, ctx, values = inputs()
    inv = Invocation()
    assert values["zone"] == "previous-reservation"
    assert program.accept(inv, ctx) is None
    assert values["zone"] == "centrifuge"
    program.start(inv, ctx)
    if ending == "cancel":
        program.cancel(inv, ctx)
    elif ending == "stop":
        stop = Invocation()
        stop.action = "stop"
        program.start(stop, ctx)
        assert stop.invocation.status == "succeeded"
    else:
        if ending == "timeout":
            ctx.sim_time = 1.0
        elif ending == "exception":

            def fail(*_):
                raise RuntimeError("Controlled delegate failure")

            program.delegate.tick = fail
        if ending == "exception":
            with pytest.raises(RuntimeError):
                program.tick(ctx, 1 / 60)
        else:
            program.tick(ctx, 1 / 60)
    assert program.handle is None
    assert inv.invocation.status in TERMINAL
    assert values["zone"] == "centrifuge"
    assert len(ctx.physics.targets) >= 9
    assert all(np.isfinite(values).all() for _, values in ctx.physics.targets)
    receipt = program.execution_evidence()
    assert receipt["actionStarts"] == 1 and receipt["callbacks"]["init"] == 1
    assert receipt["callbackErrors"] == (ending == "exception")


def test_delegate_cannot_replace_physical_facts_and_interlocks_run_before_delegate_accept():
    program, ctx, values = inputs()
    with pytest.raises(ValueError, match="Physical feedback"):
        program.delegate_context.set(zone=None)
    assert values["zone"] == "previous-reservation"
    ctx.check = lambda _: ["unavailable"]
    refused = program.accept(Invocation(), ctx)
    assert refused.code == "precondition_failed"
    assert program.execution_evidence()["callbacks"]["accept"] == 0
    assert values["zone"] == "previous-reservation"
    ctx.check = lambda _: []
    program.locations["other"] = {"site": "/other", "zone": "other", "requires": [{}]}
    assert program.accept(Invocation(), ctx).code == "precondition_failed"
    assert values["zone"] == "previous-reservation"
