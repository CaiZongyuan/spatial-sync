"""Exact expected pairs never hide undeclared physical contacts or alter raw depths."""

from types import SimpleNamespace

import pytest

from packages.programs.labware.assembly import attached_bodies
from packages.programs.labware.contacts import contacts_for_bodies, validate_expected_contacts


def test_expected_depth_is_recorded_without_clamping_but_foreign_depth_still_stops():
    expected = SimpleNamespace(bodies=(1, -1), shapes=("/tube/Floor", "/station/Floor"), distance_m=-.012)
    foreign = SimpleNamespace(bodies=(1, 3), shapes=("/tube/Floor", "/foreign/Wall"), distance_m=-.0021)
    events = []
    rows = contacts_for_bodies(
        SimpleNamespace(collision_contacts=lambda **_: [expected, foreign]), {1}, limit_m=.002,
        expected_pairs=[expected.shapes], on_expected_contact=lambda **event: events.append(event),
    )
    assert rows == [foreign]
    assert expected.distance_m == -.012
    assert events == [{"shapes": list(expected.shapes), "depthM": .012}]


@pytest.mark.parametrize("pairs", [[("/a", "/a")], [("/a/*", "/b")], [("a", "/b")],
                                  [("/a", "/b"), ("/b", "/a")]])
def test_malformed_expected_identity_fails_closed(pairs):
    with pytest.raises(ValueError):
        validate_expected_contacts(pairs)


def test_finger_candidates_do_not_exempt_other_objects_even_if_their_pair_is_declared():
    declarations = [("/finger/Pad", "/S1/Handle"), ("/finger/Pad", "/S2/Handle")]
    target = SimpleNamespace(bodies=(7, 1), shapes=declarations[0], distance_m=-.004)
    other = SimpleNamespace(bodies=(7, 2), shapes=declarations[1], distance_m=-.004)
    assert contacts_for_bodies(
        SimpleNamespace(collision_contacts=lambda **_: [target, other]), {7, 1}, limit_m=.002,
        expected_pairs=declarations, finger_bodies={7}, grasp_bodies={1},
    ) == [other]


def test_attachment_scope_is_directed_and_ignores_delayed_lid_facts():
    edges = [(-1, 1), (-1, 4), (7, 1), (1, 2)]
    held = attached_bodies({7}, edges)
    assert held == {7, 1, 2}  # No reverse path to world or its unrelated object4.
    cap_hit = SimpleNamespace(bodies=(2, 5), shapes=("/cap/Skirt", "/environment/Wall"), distance_m=-.0021)
    assert contacts_for_bodies(SimpleNamespace(collision_contacts=lambda **_: [cap_hit]), held,
                               limit_m=.002) == [cap_hit]
