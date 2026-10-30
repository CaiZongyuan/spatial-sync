"""Passage opening follows the bound physical hinge; the base device keeps 1.4 rad."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from packages.programs.labware.centrifuge import PassageCentrifugeProgram
from packages.protocol.schema import InvokeRequest
from tests.fixtures.centrifuge_cycles import RotorPort, build_cycle_host


def invoke_lid(host, action, *, legacy=False):
    return host.invoke(InvokeRequest(
        invocationId=action, requestId=action, entityKey="/cent", action=action, epoch=0,
        createdSimTime=host.sim_time, createdAtMs=0,
        params={} if legacy else {"owner": "auto", "epoch": 0, "operationId": action},
    ))


def test_passage_target_completion_and_open_fact_share_physical_upper(tmp_path, monkeypatch):
    upper = math.radians(100)
    monkeypatch.setattr(RotorPort, "joint_limits", lambda *_: (np.array([0.]), np.array([upper])))
    host, port, _ = build_cycle_host(tmp_path)
    assert invoke_lid(host, "openLid").status == "accepted"
    host.tick(.1, .1)
    assert port.lid_target == upper
    port.lid_q = 1.4
    host.tick(.2, .1)
    assert host.values()["/cent"]["lidOpen"] is False
    assert next(r for r in host.snapshot().invocations if r.invocationId == "openLid").status == "running"
    port.lid_q = upper
    host.tick(.3, .1)
    assert host.values()["/cent"]["lidOpen"] is True
    assert next(r for r in host.snapshot().invocations if r.invocationId == "openLid").status == "succeeded"
    port.lid_qd = .03
    host.tick(.4, .1)
    assert host.values()["/cent"]["lidOpen"] is False
    port.lid_qd = 0
    assert invoke_lid(host, "closeLid").status == "accepted"
    host.tick(.5, .1)
    assert port.lid_target == 0


def test_base_open_target_does_not_require_or_follow_joint_limits(tmp_path, monkeypatch):
    def unavailable(*_):
        raise AssertionError("Base centrifuge must retain its existing target")

    monkeypatch.setattr(RotorPort, "joint_limits", unavailable)
    host, port, _ = build_cycle_host(tmp_path, legacy=True)
    assert invoke_lid(host, "openLid", legacy=True).status == "accepted"
    host.tick(.1, .1)
    assert port.lid_target == 1.4
    port.lid_q = 1.4
    host.tick(.2, .1)
    assert next(r for r in host.snapshot().invocations if r.invocationId == "openLid").status == "succeeded"


@pytest.mark.parametrize("limits", [
    ([], []), ([0, 0], [1.4, 1.4]), (0., 1.4), ([0], [math.inf]),
    ([math.nan], [1.4]), ([-math.inf], [1.4]), ([0], [0]), ([0], [-1.4]),
    ([.1], [1.4]), ([0], [True]), ([0], ["1.4"]),
])
def test_invalid_passage_lid_limits_fail_before_any_actuation(limits):
    writes = []
    port = SimpleNamespace(bind_joint=lambda key: key, joint_limits=lambda _: limits,
                           set_joint_target=lambda *args: writes.append(args))
    ctx = SimpleNamespace(physics=port, config=SimpleNamespace(spindleJoint="spindle", lidJoint="lid"))
    with pytest.raises(ValueError, match="one finite joint range"):
        PassageCentrifugeProgram().init(ctx)
    assert writes == []
