"""A real reported occupant remains blocking even if its resource was reserved."""

from types import SimpleNamespace

import numpy as np

from packages.programs.labware.motion import LabwareMotionProgram


def configured_context():
    values = {}
    bodies = {"hand": 0, "held": 1, "occupant": 2}
    joints = {
        key: SimpleNamespace(child=index)
        for index, key in enumerate([*[f"j{i}" for i in range(7)], "f1", "f2"])
    }
    physics = SimpleNamespace(
        body=np.array([[0, 0, 0, 0, 0, 0, 1]] * 3, dtype=float),
        velocity=np.zeros((3, 3)),
        bind_joint=lambda key: joints[key],
        bind_body=lambda key: bodies[key],
        bind_site=lambda key: key,
        joint_q=lambda joint: np.array([0.008 if joint.child >= 7 else 0]),
        joint_qd=lambda joint: np.zeros(1),
        site_pose=lambda key: (np.zeros(3), np.array([0, 0, 0, 1])),
        welds=SimpleNamespace(active=lambda hand, body: body == 1),
    )
    cfg = LabwareMotionProgram.Config(
        controlEntity="workcell",
        tubes=["held", "occupant"],
        graspOffsets={"held": [0, 0, 0.035], "occupant": [0, 0, 0.035]},
        gripDiameters={"held": 0.017, "occupant": 0.017},
        gripShapePrefixes={"held": "held/Handle", "occupant": "occupant/Handle"},
        locations={
            "locations": {
                "target": {"site": "targetSite", "zone": "rack", "approach": [0.0] * 7, "grasp": [0.0] * 7}
            }
        },
        jointKeys=[f"j{i}" for i in range(7)],
        fingerKeys=["f1", "f2"],
        fingerPadShapes=["left/Pad", "right/Pad"],
        handBody="hand",
        tcpSite="tcp",
    )
    state = {
        "controlMode": "auto",
        "controlOwner": "auto",
        "controlEpoch": 0,
        "materialSnapshot": {
            "reservations": [
                {"resource": key, "device": "robot", "owner": "auto", "epoch": 0}
                for key in ("targetSite", "held", "occupant")
            ]
        },
    }
    ctx = SimpleNamespace(
        key="robot",
        config=cfg,
        physics=physics,
        set=lambda **x: values.update(x),
        get=lambda key: values.get(key),
        check=lambda rules: [],
        read=lambda key, prop: (
            state[prop] if key == "workcell" else "targetSite" if key == "occupant" else "held"
        ),
    )
    return ctx, values


def test_place_rejects_an_already_occupied_actual_station_before_motion():
    ctx, values = configured_context()
    program = LabwareMotionProgram()
    program.init(ctx)
    inv = SimpleNamespace(
        action="place", params={"location": "target", "owner": "auto", "epoch": 0, "operationId": "place"}
    )
    rejection = program.accept(inv, ctx)
    assert rejection and rejection.code == "station_occupied"


def test_payload_only_deep_contact_holds_actual_targets_and_fails_without_unwelding():
    ctx, values = configured_context()
    writes, failures = {}, []
    ctx.fact_due = True
    ctx.emit = lambda *args, **kwargs: None
    ctx.physics.set_joint_target = lambda joint, target: writes.update({joint.child: target[0]})
    ctx.physics.collision_contacts = lambda **kwargs: [
        SimpleNamespace(bodies=(-1, 1), shapes=("/World/Obstacle", "held/Floor"), distance_m=-0.002)
    ]
    program = LabwareMotionProgram()
    program.init(ctx)
    request = SimpleNamespace(
        action="moveTo",
        params={"location": "target", "owner": "auto", "epoch": 0, "operationId": "move"},
        fail=lambda *args, **kwargs: failures.append(args[0]),
        progress=lambda value: None,
    )
    program.start(request, ctx)
    program.tick(ctx, 1 / 60)
    assert failures == ["unsafe_contact"]
    assert values["holding"] == "held"
    assert values["unsafeContactCount"] == 1
    assert values["lastContactShapes"] == ["/World/Obstacle", "held/Floor"]
    assert all(writes[index] == 0 for index in range(7))


def test_unknown_contact_backend_fails_closed_and_retains_the_payload():
    ctx, values = configured_context()
    failures = []
    ctx.fact_due = True
    ctx.physics.set_joint_target = lambda joint, target: None

    def unavailable(**kwargs):
        raise ValueError("No supported contact facts")

    ctx.physics.collision_contacts = unavailable
    program = LabwareMotionProgram()
    program.init(ctx)
    request = SimpleNamespace(
        action="moveTo",
        params={"location": "target", "owner": "auto", "epoch": 0, "operationId": "move"},
        fail=lambda *args, **kwargs: failures.append(args[0]),
    )
    program.start(request, ctx)
    program.tick(ctx, 1 / 60)
    assert failures == ["contact_unavailable"]
    assert values["holding"] == "held"
