"""Actual tip body and moving dock/socket facts gate the physical weld."""

import math

import pytest

from tests.fixtures.cell_passage import build_workcell_host
from tests.unit.test_cell_passage import invoke


@pytest.mark.parametrize(
    "position,quaternion",
    [
        ([0.02, 0, 0], [0, 0, 0, 1]),
        ([0, 0, 0], [1, 0, 0, 0]),
        ([0, 0, 0], [0, math.sin(0.05), 0, math.cos(0.05)]),
        ([float("nan"), 0, 0], [0, 0, 0, 1]),
        ([0, 0, 0], [0, 0, 0, 0]),
        (None, [0, 0, 0, 1]),
    ],
)
def test_invalid_actual_tip_pose_cannot_be_welded_from_an_unused_static_label(tmp_path, position, quaternion):
    host, port = build_workcell_host(tmp_path)
    port.body_positions["/tip1"], port.body_quaternions["/tip1"] = position, quaternion
    before = host.values()["/workcell"]["materialSnapshot"]
    result = invoke(host, "pickupTip", "pick", tip="T1")
    if result.status == "accepted":
        host.tick(0.1, 0.1)
    assert not port.welds
    assert result.status == "rejected"
    assert host.values()["/workcell"]["materialSnapshot"] == before


def test_two_individually_near_dock_points_do_not_allow_a_head_socket_clearance_violation(tmp_path):
    host, port = build_workcell_host(tmp_path, config={"alignmentToleranceM": 0.0003})
    port.body_positions["/tip1"] = [0.00029, 0, 0]
    port.positions["tip"] = [-0.00029, 0, 0]
    before = host.values()["/workcell"]["materialSnapshot"]
    result = invoke(host, "pickupTip", "pick", tip="T1")
    if result.status == "accepted":
        host.tick(0.1, 0.1)
    assert not port.welds and result.status == "rejected"
    assert host.values()["/workcell"]["materialSnapshot"] == before


def test_dynamic_dock_pose_and_axis_symmetric_yaw_can_mate_normally(tmp_path):
    host, port = build_workcell_host(tmp_path, config={"alignmentToleranceM": 0.0003})
    for key in ("/T1", "tip"):
        port.positions[key] = [1, 2, 3]
    port.body_positions["/tip1"] = [1, 2, 3]
    port.body_quaternions["/tip1"] = [0, 0, math.sin(0.7), math.cos(0.7)]
    result = invoke(host, "pickupTip", "pick", tip="T1")
    assert result.status == "accepted"
    host.tick(0.1, 0.1)
    assert ("/pipette", "/tip1") in port.welds
    assert host.values()["/workcell"]["materialSnapshot"]["tip"]["id"] == "T1"


def test_actual_nozzle_segment_must_fit_the_socket_mouth_not_just_the_seat_point(tmp_path):
    host, port = build_workcell_host(tmp_path, config={"alignmentToleranceM": 0.0003})
    port.positions["tip"] = [-0.00029, 0, 0]
    angle = 0.0116
    port.quaternions["tip"] = [0, math.sin(angle / 2), 0, math.cos(angle / 2)]
    before = host.values()["/workcell"]["materialSnapshot"]
    result = invoke(host, "pickupTip", "pick", tip="T1")
    assert result.status == "rejected" and result.error.code == "tip_misaligned"
    assert "radial/axial" in result.error.message
    assert not port.welds and host.values()["/workcell"]["materialSnapshot"] == before


@pytest.mark.parametrize(
    "condition", ["unknown_geometry", "no_gap", "moving_tip", "moving_head", "unknown_dock"]
)
def test_unknown_or_unseated_assembly_conditions_do_not_consume_a_tip(tmp_path, condition):
    host, port = build_workcell_host(
        tmp_path, config={"tipGeometry": None} if condition == "unknown_geometry" else {}
    )
    if condition == "no_gap":
        # Read the same-source fixture descriptor, then use a distinct host with
        # a socket that cannot geometrically fit the specified nozzle.
        geometry = host.catalog.entities["/workcell"]["config"]["tipGeometry"]
        geometry["socketInnerDiameterM"] = geometry["nozzleOuterDiameterM"]
        directory = tmp_path / "no-clearance"
        directory.mkdir()
        host, port = build_workcell_host(directory, config={"tipGeometry": geometry})
    elif condition == "moving_tip":
        port.body_velocities["/tip1"] = [0.02, 0, 0]
    elif condition == "moving_head":
        port.velocities["x"] = 0.01
    elif condition == "unknown_dock":
        port.quaternions["/T1"] = [0, 0, 0, 0]
    before = host.values()["/workcell"]["materialSnapshot"]
    result = invoke(host, "pickupTip", "pick", tip="T1")
    assert result.status == "rejected"
    assert not port.welds and host.values()["/workcell"]["materialSnapshot"] == before


def test_body_pose_is_rechecked_at_start_after_intake_was_valid(tmp_path):
    host, port = build_workcell_host(tmp_path)
    before = host.values()["/workcell"]["materialSnapshot"]
    assert invoke(host, "pickupTip", "pick", tip="T1").status == "accepted"
    port.body_positions["/tip1"] = [0.02, 0, 0]
    host.tick(0.1, 0.1)
    outcome = next(row for row in host.snapshot().invocations if row.invocationId == "pick")
    assert outcome.status == "failed" and outcome.error.code == "tip_not_seated"
    assert not port.welds and host.values()["/workcell"]["materialSnapshot"] == before


def test_dynamic_dock_moving_away_from_unmoved_tip_cannot_be_satisfied_by_a_static_label(tmp_path):
    host, port = build_workcell_host(tmp_path)
    port.positions["/T1"] = [0.01, 0, 0]
    port.positions["tip"] = [0.01, 0, 0]
    result = invoke(host, "pickupTip", "pick", tip="T1")
    assert result.status == "rejected" and not port.welds
