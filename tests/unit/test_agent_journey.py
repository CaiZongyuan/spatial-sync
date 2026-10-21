"""The I5 runner accepts only a causally complete I3 journey."""

from tools.agent_journey import journey_order

ROBOT, CENTRIFUGE = "/World/Lab/RobotA", "/World/Lab/Centrifuge"


def invocation(key, action, params=None, status="succeeded"):
    return {"entityKey": key, "action": action, "params": params or {}, "status": status}


def pick_place(source, target):
    return invocation(ROBOT, "pickPlace", {"from": source, "to": target})


JOURNEY = [
    pick_place("rack.A1", "centrifuge.slot1"),
    pick_place("rack.A2", "centrifuge.slot3"),
    invocation(ROBOT, "home"),
    invocation(CENTRIFUGE, "closeLid"),
    invocation(CENTRIFUGE, "start", {"rpm": 3000, "durationSeconds": 20}),
    invocation(CENTRIFUGE, "openLid"),
    pick_place("centrifuge.slot1", "rack.A1"),
    pick_place("centrifuge.slot3", "rack.A2"),
]


def test_complete_journey_with_a_rejection_is_ordered():
    rejected = invocation(CENTRIFUGE, "closeLid", status="rejected")
    result = journey_order([*JOURNEY[:2], rejected, *JOURNEY[2:]])
    assert result["ordered"]
    assert result["steps"]["closeLid"] == 3


def test_spinning_before_the_lid_closes_is_not_ordered():
    assert not journey_order([*JOURNEY[:2], JOURNEY[4], JOURNEY[3], *JOURNEY[5:]])["ordered"]


def test_a_missing_return_is_not_ordered():
    assert not journey_order(JOURNEY[:-1])["ordered"]


def test_other_parameters_or_devices_do_not_count():
    slow = invocation(CENTRIFUGE, "start", {"rpm": 500, "durationSeconds": 20})
    assert not journey_order([*JOURNEY[:4], slow, *JOURNEY[5:]])["ordered"]
    elsewhere = invocation("/World/Lab/Other", "closeLid")
    assert not journey_order([*JOURNEY[:3], elsewhere, *JOURNEY[4:]])["ordered"]
