"""Modeled signals must read physical doors and identify their modeled source."""

import random
from types import SimpleNamespace

import pytest

from packages.programs.labware.environment import PassageCabinetProgram, PassageIncubatorProgram


def incubator_context(q=0.0, velocity=0.0):
    values = {}
    physics = SimpleNamespace(q=q, qd=velocity, bind_joint=lambda key: key)
    physics.joint_q = lambda _: [physics.q]
    physics.joint_qd = lambda _: [physics.qd]
    control = {
        "controlMode": "auto",
        "controlOwner": "auto",
        "controlEpoch": 0,
        "materialSnapshot": {
            "reservations": [{"resource": "incubator", "device": "incubator", "owner": "auto", "epoch": 0}]
        },
    }
    return SimpleNamespace(
        config=PassageIncubatorProgram.Config(
            controlEntity="workcell", outerDoorJoint="outer", innerDoorJoint="inner", timeConstantSeconds=10
        ),
        key="incubator",
        read=lambda entity, prop: control[prop],
        control=control,
        physics=physics,
        set=lambda **kwargs: values.update(kwargs),
        get=lambda key: values[key],
        rng=random.Random(1),
        fact_due=True,
        values=values,
    )


def test_actual_open_or_moving_door_overrides_static_config_and_blocks_ready():
    for q, velocity in ((1.0, 0.0), (0.001, 0.1), (float("nan"), 0.0)):
        ctx, program = incubator_context(q, velocity), PassageIncubatorProgram()
        program.init(ctx)
        program.tick(ctx, 1.0)
        assert ctx.values["doorOpen"] is True
        assert ctx.values["environmentReady"] is False
        assert ctx.values["signalSource"] == "modeled"


def test_closed_prepared_incubator_reports_protocol_conditions_and_mechanical_closed_state():
    ctx, program = incubator_context(), PassageIncubatorProgram()
    program.init(ctx)
    assert ctx.values["environmentReady"] is True
    assert ctx.values["doorOpen"] is False
    assert ctx.values["doorKnown"] is True
    assert ctx.values["temperatureC"] == 37.0
    assert ctx.values["co2Percent"] == 5.0
    assert ctx.values["humidityPercent"] == 95.0
    assert ctx.values["humiditySetpointPercent"] == 95.0
    assert ctx.values["humidityReadyRangePercent"] == [90, 100]
    program.tick(ctx, 600.0)
    assert 94.9 < ctx.values["humidityPercent"] < 95.1


def test_cabinet_ready_requires_modeled_blower_flow_uv_and_sash_together():
    for config, expected in (
        ({}, True),
        ({"blowerOn": False}, False),
        ({"uvOn": True}, False),
        ({"initialInflowMs": 0.2}, False),
    ):
        values = {}
        ctx = SimpleNamespace(
            config=PassageCabinetProgram.Config(controlEntity="workcell", **config),
            set=lambda **kwargs: values.update(kwargs),
            get=lambda key: values[key],
            rng=random.Random(1),
            fact_due=True,
        )
        PassageCabinetProgram().init(ctx)
        assert values["cabinetReady"] is expected
        assert values["signalSource"] == "modeled"


def test_wait_ready_uses_simulation_recovery_and_actual_closed_doors():
    ctx, program = incubator_context(1.0), PassageIncubatorProgram()
    program.init(ctx)
    program.tick(ctx, 20.0)
    statuses = []
    inv = SimpleNamespace(
        id="wait",
        action="waitReady",
        params={"owner": "auto", "epoch": 0, "operationId": "recover", "timeoutSeconds": 90},
        succeed=lambda: statuses.append("succeeded"),
        fail=lambda code, message: statuses.append(code),
    )
    program.start(inv, ctx)
    assert statuses == []
    ctx.physics.q = 0.0
    for _ in range(60):
        program.tick(ctx, 1.0)
        if statuses:
            break
    assert statuses == ["succeeded"]
    assert ctx.values["environmentReady"] is True
    assert 0 < ctx.values["waitElapsedSeconds"] < 60


def test_legacy_incubator_keeps_its_sixty_percent_closed_target():
    from packages.programs.incubator.virtual import IncubatorProgram

    values = {}
    ctx = SimpleNamespace(
        config=IncubatorProgram.Config(),
        set=lambda **kwargs: values.update(kwargs),
        get=lambda key: values[key],
        rng=random.Random(1),
        fact_due=True,
    )
    program = IncubatorProgram()
    program.init(ctx)
    program.tick(ctx, 600.0)
    assert 59.9 < values["humidityPercent"] < 60.1


def test_custom_teaching_target_and_public_range_drive_the_same_readiness_model():
    ctx, program = incubator_context(), PassageIncubatorProgram()
    ctx.config = PassageIncubatorProgram.Config(
        controlEntity="workcell",
        outerDoorJoint="outer",
        innerDoorJoint="inner",
        initialHumidityPercent=80,
        targetHumidityPercent=80,
        humidityRangePercent=[75, 85],
    )
    program.init(ctx)
    program.tick(ctx, 600.0)
    assert ctx.values["environmentReady"] is True
    assert 79.9 < ctx.values["humidityPercent"] < 80.1
    assert ctx.values["humidityReadyRangePercent"] == [75, 85]


@pytest.mark.parametrize("stale,expected", [(False, "readiness_timeout"), (True, "stale_control")])
def test_wait_ready_timeout_or_old_owner_never_becomes_late_success(stale, expected):
    ctx, program = incubator_context(1.0), PassageIncubatorProgram()
    program.init(ctx)
    statuses = []
    inv = SimpleNamespace(
        id="wait",
        action="waitReady",
        params={"owner": "auto", "epoch": 0, "operationId": "recover", "timeoutSeconds": 1},
        succeed=lambda: statuses.append("succeeded"),
        fail=lambda code, message: statuses.append(code),
    )
    program.start(inv, ctx)
    if stale:
        ctx.control["controlEpoch"] = 1
    program.tick(ctx, 1.0)
    assert statuses == [expected]
    ctx.physics.q = 0.0
    program.tick(ctx, 120.0)
    assert statuses == [expected]
    assert ctx.values["phase"] == "idle"
