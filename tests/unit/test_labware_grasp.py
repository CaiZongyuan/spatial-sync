"""Physical hand checks for the raised carrier, independently measured dimensions."""

import math

import pytest

from packages.programs.labware.grasp import grasp_geometry


def test_carrier_handle_fits_but_standard_plate_width_does_not():
    tcp = [0, 0, 0, 0, 0, 0, 1]
    body = [-0.085, 0, -0.075, 0, 0, 0, 1]
    assert grasp_geometry(body, tcp, [0.085, 0, 0.075], 0.017, 0.018)
    assert not grasp_geometry(body, tcp, [0.085, 0, 0.075], 0.08548, 0.08)


def test_weld_requires_closed_fingers_and_actual_moving_grasp_frame():
    body = [0, 0, 0, 0, 0, math.sqrt(0.5), math.sqrt(0.5)]
    tcp = [0, 0.085, 0.075, 0, 0, 0, 1]
    assert grasp_geometry(body, tcp, [0.085, 0, 0.075], 0.017, 0.018)
    assert not grasp_geometry(body, tcp, [0.085, 0, 0.075], 0.017, 0.08)
    assert not grasp_geometry(body, tcp, [0.085, 0, 0.075], 0.017, 0)
    assert not grasp_geometry(body, [0, 0, 0.075, 0, 0, 0, 1], [0.085, 0, 0.075], 0.017, 0.018)


@pytest.mark.parametrize("width", [-0.001, float("nan"), 0.081])
def test_invalid_grip_width_cannot_qualify(width):
    assert not grasp_geometry([0, 0, 0, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0, 1], [0, 0, 0], width, 0.017)
