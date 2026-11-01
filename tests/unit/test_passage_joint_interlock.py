"""Raw move and named door actions must share the same physical interlock."""

from types import SimpleNamespace

from packages.programs.labware.joints import PassageJointProgram


def context():
    key = "inner"
    props = {
        "controlMode": "auto",
        "controlOwner": "auto",
        "controlEpoch": 0,
        "materialSnapshot": {"reservations": [{"resource": key, "device": key, "owner": "auto", "epoch": 0}]},
    }
    q = [0.0]
    physics = SimpleNamespace(bind_joint=lambda _: "hinge", joint_q=lambda _: q, joint_qd=lambda _: [0.0])
    values = {}
    return SimpleNamespace(
        key=key,
        physics=physics,
        values=values,
        config=PassageJointProgram.Config(
            controlEntity="workcell",
            jointKey="hinge",
            lower=0,
            upper=1.745,
            maxSpeed=0.4,
            role="inner",
            peerDoor="outer",
        ),
        set=lambda **updates: values.update(updates),
        read=lambda entity, prop: (
            props[prop] if entity == "workcell" else {"opened": False, "phase": "idle"}[prop]
        ),
    )


def test_raw_inner_joint_move_cannot_bypass_outer_door_interlock():
    ctx, program = context(), PassageJointProgram()
    program.init(ctx)
    request = SimpleNamespace(
        action="move", params={"position": 1.2, "owner": "auto", "epoch": 0, "operationId": "raw-open"}
    )
    rejection = program.accept(request, ctx)
    assert rejection and rejection.code == "door_interlock"
