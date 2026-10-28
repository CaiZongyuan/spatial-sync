"""Contact identity is independent of the solver's sanitized display names."""

from types import SimpleNamespace

import pytest

from packages.programs.labware.grasp import physical_grasp


def contact(finger, distance, shape="plate/Handle"):
    return SimpleNamespace(bodies=(13, finger), shapes=(shape, f"finger{finger}/Pad"), distance_m=distance)


def qualify(contacts, opening=0.01431):
    return physical_grasp(
        [-0.085, 0, -0.075, 0, 0, 0, 1],
        [0, 0, 0, 0, 0, 0, 1],
        [0.085, 0, 0.075],
        0.017,
        opening,
        contacts,
        body=13,
        fingers={11, 12},
        shape_prefix="plate/Handle",
    )


def test_bilateral_real_pad_contact_can_qualify_below_nominal_joint_opening():
    assert qualify([contact(11, -0.000632), contact(12, -0.000128)])


@pytest.mark.parametrize(
    "contacts",
    [
        [],
        [contact(11, -0.0002)],
        [contact(11, 0.0002), contact(12, -0.0002)],
        [contact(11, -0.0002, "plate/WellA1"), contact(12, -0.0002)],
        [contact(11, -0.0011), contact(12, -0.0002)],
        [contact(11, float("nan")), contact(12, -0.0002)],
    ],
)
def test_missing_margin_only_wrong_surface_or_deep_contact_cannot_qualify(contacts):
    assert not qualify(contacts)


def test_open_gripper_cannot_qualify_even_with_stale_contacts():
    assert not qualify([contact(11, -0.0002), contact(12, -0.0002)], opening=0.08)
