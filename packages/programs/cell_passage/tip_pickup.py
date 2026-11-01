"""Fit the actual head and stock tip using the factory's same-source geometry."""

import numpy as np
from pydantic import Field, FiniteFloat, model_validator

from packages.info.program import ProgramConfig, Reject
from packages.protocol.math import rotate_vector

Vector = tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class TipGeometry(ProgramConfig):
    tipMatingOffsetLocalM: Vector
    tipAxisLocal: Vector
    headMatingOffsetLocalM: Vector
    headAxisLocal: Vector
    socketDepthM: FiniteFloat = Field(gt=0)
    socketInnerDiameterM: FiniteFloat = Field(gt=0)
    nozzleOuterDiameterM: FiniteFloat = Field(gt=0)

    @model_validator(mode="after")
    def mating_frames(self):
        for axis, offset in (
            (self.tipAxisLocal, self.tipMatingOffsetLocalM),
            (self.headAxisLocal, self.headMatingOffsetLocalM),
        ):
            if abs(np.linalg.norm(axis) - 1) > 1e-6 or np.dot(axis, offset) <= 0:
                raise ValueError("Mating axes must be unit vectors pointing from endpoint toward socket")
        return self


def _pose(value):
    p, q = (np.asarray(part, dtype=float) for part in value)
    if (
        p.shape != (3,)
        or q.shape != (4,)
        or not np.isfinite(p).all()
        or not np.isfinite(q).all()
        or abs(np.linalg.norm(q) - 1) > 1e-3
    ):
        raise ValueError("Actual finite unit-quaternion pose required")
    return p, q


def check_pickup(port, geometry, tip_body, dock_sites, head_site, alignment_tolerance):
    if geometry is None:
        return Reject(code="tip_geometry_unavailable", message="Same-source tip/nozzle geometry is required")
    clearance = (geometry.socketInnerDiameterM - geometry.nozzleOuterDiameterM) / 2
    if clearance <= 0:
        return Reject(code="incompatible_tip", message="No straight socket/nozzle assembly clearance exists")
    try:
        tip_p, tip_q = _pose(port.body_pose(tip_body))
        dock_p, dock_q = _pose(port.site_pose(dock_sites[0]))
        head_p, head_q = _pose(port.site_pose(head_site))
        relative_velocity = np.asarray(port.relative_site_velocity(tip_body, dock_sites), dtype=float)
        if relative_velocity.shape != (1, 3) or not np.isfinite(relative_velocity).all():
            raise ValueError("Actual tip/dock relative velocity required")
        tip_axis = np.asarray(rotate_vector(tip_q, geometry.tipAxisLocal))
        dock_axis = np.asarray(rotate_vector(dock_q, geometry.tipAxisLocal))
        head_axis = np.asarray(rotate_vector(head_q, geometry.headAxisLocal))
        seat = tip_p + rotate_vector(tip_q, geometry.tipMatingOffsetLocalM)
        dock_seat = dock_p + rotate_vector(dock_q, geometry.tipMatingOffsetLocalM)
        head_seat = head_p + rotate_vector(head_q, geometry.headMatingOffsetLocalM)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return Reject(
            code="tip_pose_unknown", message="Actual tip, dock and head pose/velocity must be known"
        )
    tolerance = min(alignment_tolerance, clearance)
    if (
        np.linalg.norm(relative_velocity[0]) >= 0.01
        or np.linalg.norm(tip_p - dock_p) > tolerance
        or np.linalg.norm(seat - dock_seat) > tolerance
        or np.dot(tip_axis, dock_axis) <= 0
    ):
        return Reject(code="tip_not_seated", message="Unused tip is not actually settled upright in its dock")
    if (
        np.linalg.norm(head_p - tip_p) > tolerance
        or np.linalg.norm(head_seat - seat) > tolerance
        or np.dot(tip_axis, head_axis) <= 0
    ):
        return Reject(code="tip_misaligned", message="Actual head endpoint and socket seat are not aligned")
    # Both actual segment ends must fit the straight bore. Checking each part
    # independently against the dock could add two errors larger than the gap.
    for point, expected_depth in (
        (head_seat, 0.0),
        (head_seat + head_axis * geometry.socketDepthM, geometry.socketDepthM),
    ):
        delta = point - seat
        depth = float(np.dot(delta, tip_axis))
        radial = np.linalg.norm(delta - depth * tip_axis)
        if radial >= clearance or abs(depth - expected_depth) > tolerance:
            return Reject(
                code="tip_misaligned", message="Actual nozzle segment exceeds socket radial/axial clearance"
            )
    return None
