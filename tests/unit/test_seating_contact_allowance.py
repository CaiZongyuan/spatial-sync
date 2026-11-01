"""Only explicitly declared corresponding rack floors may use the demo seating band."""

from types import SimpleNamespace

import pytest

from packages.programs.labware.contacts import CONTACT_LIMIT_M, contacts_for_bodies, validate_seating_contacts
from packages.programs.labware.motion import LabwareMotionProgram
from tests.fixtures.passage_joint_host import build_joint_host
from tests.unit.test_labware_occupancy import configured_context
from tests.unit.test_passage_joint_safety import invoke

PAIR = ("/World/Lab/Labware/S1/Floor", "/World/Lab/Labware/CollectionRack/Frame/S1Floor")


def row(depth, shapes=PAIR, bodies=(1, -1)):
    return SimpleNamespace(bodies=bodies, shapes=shapes, distance_m=-depth)


def test_exact_pair_band_keeps_raw_depth_and_emits_deepest_actual_row_once():
    rows = [row(.0027), row(.0038, PAIR[::-1])]
    observed, events = [], []

    def query(**kwargs):
        observed.append(kwargs)
        return rows

    assert contacts_for_bodies(SimpleNamespace(collision_contacts=query), {1}, limit_m=.002,
                               seating_pairs=[PAIR], seating_limit_m=.005,
                               on_seating_contact=lambda **data: events.append(data)) == []
    assert observed == [{"max_distance": -.002, "bodies": {1}}]
    assert [r.distance_m for r in rows] == [-.0027, -.0038]
    assert events == [{"shapes": list(PAIR[::-1]), "depthM": .0038, "limitM": .005}]


@pytest.mark.parametrize("contact,pairs", [
    (row(.005001), [PAIR]), (row(.0038), []),
    (row(.0038, ("/World/Robot/Arm", PAIR[1])), [PAIR]),
    (row(.0038, (PAIR[0], "/World/Lab/Labware/CollectionRack/Frame/S2Floor")), [PAIR]),
])
def test_deep_or_undeclared_contacts_remain_unsafe_without_clamping(contact, pairs):
    events = []
    assert contacts_for_bodies(SimpleNamespace(collision_contacts=lambda **_: [contact]), {1}, limit_m=.002,
                               seating_pairs=pairs, seating_limit_m=.005,
                               on_seating_contact=lambda **data: events.append(data)) == [contact]
    assert events == []


def test_absent_exception_pair_and_exact_five_mm_boundary():
    assert CONTACT_LIMIT_M == .001
    events = []
    for contact in (row(.005), row(.001, ("/other/a", "/other/b"))):
        assert contacts_for_bodies(SimpleNamespace(collision_contacts=lambda **_: [contact]), {1},
                                   limit_m=.002, seating_pairs=[PAIR], seating_limit_m=.005,
                                   on_seating_contact=lambda **data: events.append(data)) == []
    assert events == [{"shapes": list(PAIR), "depthM": .005, "limitM": .005}]


@pytest.mark.parametrize("pairs,limit", [
    ([PAIR], .005001), ([PAIR], float("nan")), ([PAIR], True), ([PAIR, PAIR[::-1]], .005),
    ([(PAIR[0] + "Typo", PAIR[1])], .005),
    ([(PAIR[0], PAIR[1].replace("S1Floor", "S2Floor"))], .005),
    ([("/World/Robot/Floor", PAIR[1])], .005),
])
def test_invalid_or_typo_declarations_fail_closed(pairs, limit):
    with pytest.raises(ValueError):
        validate_seating_contacts(pairs, limit)


@pytest.mark.parametrize("depth,shapes,allowed", [
    (.0038, PAIR, True), (.005001, PAIR, False),
    (.0038, ("/World/Robot/Arm", PAIR[1]), False),
])
def test_motion_consumer_applies_only_exact_pair_and_records_accepted_depth(depth, shapes, allowed):
    ctx, values = configured_context()
    ctx.config = LabwareMotionProgram.Config.model_validate(ctx.config.model_dump() | {
        "contactAllowanceM": .002, "seatingContactPairs": [PAIR], "seatingContactAllowanceM": .005,
    })
    ctx.fact_due = True
    events, failures = [], []
    ctx.emit = lambda name, **data: events.append((name, data))
    ctx.physics.collision_contacts = lambda **_: [row(depth, shapes)]
    program = LabwareMotionProgram()
    program.init(ctx)
    program._hold = lambda _: None
    program._end = lambda _, error, **data: failures.append((error, data))
    assert program._contact_guard(ctx) is allowed
    if allowed:
        assert events == [("seatingContact", {"shapes": list(PAIR), "depthM": depth, "limitM": .005})]
        ctx.fact_due = False
        assert program._contact_guard(ctx)
        assert len(events) == 2  # A short accepted contact cannot disappear between fact publications.
    else:
        assert events[0][0] == "unsafeContact" and events[0][1]["depthM"] == depth
        assert failures[0][1]["depthM"] == depth
        assert values["lastContactDepthM"] == depth


@pytest.mark.parametrize("depth,accepted", [(.0038, True), (.005001, False)])
def test_joint_host_consumer_records_real_pair_depth_without_releasing_payload(tmp_path, depth, accepted):
    host, port = build_joint_host(tmp_path, safety_config={
        "contactAllowanceM": .002, "seatingContactPairs": [PAIR], "seatingContactAllowanceM": .005,
    })
    port.contacts = [row(depth, bodies=("/cap", "/obstacle"))]
    result = invoke(host, "move", position=.5)
    assert (result.status == "accepted") is accepted
    assert port.welds == {("/armbody", "/cap")}
    events = host.tick(.1, .1)
    if accepted:
        seating = [e for e in events if getattr(e, "event", None) == "seatingContact"]
        assert seating and seating[0].data == {"shapes": list(PAIR), "depthM": depth, "limitM": .005}
    else:
        assert result.error.code == "unsafe_contact" and result.error.details["depthM"] == depth


def test_physical_grasp_rejects_bilateral_three_point_eight_mm_even_with_demo_seating_config():
    from packages.programs.labware.grasp import physical_grasp

    contacts = [row(.0038, ("tube/Handle", f"finger{i}/Pad"), (13, i)) for i in (11, 12)]
    assert not physical_grasp(
        [-.085, 0, -.075, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0, 1], [.085, 0, .075], .017, .014,
        contacts, body=13, fingers={11, 12}, pad_shapes={i: f"finger{i}/Pad" for i in (11, 12)},
        shape_prefix="tube/Handle", contact_allowance_m=.002,
    )
