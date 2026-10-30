"""Measured opt-in handoffs: no pose writes, no unqualified attach or early release."""

from types import SimpleNamespace

import numpy as np
import pytest

from packages.programs.labware.assembly import attached_bodies
from packages.programs.labware.placement import QualifiedPlacements


class Port:
    def __init__(self):
        self.keys = {"/tube": 0, "/cap": 1, "/other": 2, "/hand": 3}
        self.body = np.array(
            [[0, 0, 0, 0, 0, 0, 1], [0, 0, 0.05, 0, 0, 0, 1], [4, 0, 0, 0, 0, 0, 1], [8, -4, 3, 0, 0, 0, 1]],
            dtype=float,
        )
        self.velocity = np.zeros((4, 3))
        self.enabled = set()
        self.changes = []
        self.contacts = []
        self.welds = SimpleNamespace(
            active=lambda parent, child: (parent, child) in self.enabled,
            attached_bodies=lambda roots: attached_bodies(roots, self.enabled),
        )

    def bind_body(self, key):
        return self.keys[key]

    def bind_site(self, key):
        return SimpleNamespace(key=key, body=0 if key == "/tube/LidSeat" else -1)

    def bind_sites(self, keys):
        return tuple(self.bind_site(key) for key in keys)

    def site_poses(self, sites):
        return np.array(
            [
                np.r_[self.body[0, :3] + [0, 0, 0.05], self.body[0, 3:]]
                if site.key == "/tube/LidSeat"
                else [1 if site.key == "/destination" else 0, 0, 0, 0, 0, 0, 1]
                for site in sites
            ]
        )

    def relative_site_velocity(self, body, sites):
        return np.array([self.velocity[body] for _ in sites])

    def collision_contacts(self, **kwargs):
        return self.contacts

    def weld(self, parent, body):
        self.enabled.add((parent, body))
        self.changes.append(("attach", parent, body))

    def unweld(self, parent, body):
        self.enabled.remove((parent, body))
        self.changes.append(("detach", parent, body))


def context():
    port = Port()
    events = []
    cfg = SimpleNamespace(
        tubes=["/tube", "/cap"],
        placementStations={"/tube": ["/source", "/destination"], "/cap": ["/tube/LidSeat"]},
        initialPlacements={"/tube": "/source", "/cap": "/tube/LidSeat"},
        expectedContactPairs=[],
        contactAllowanceM=0.002,
    )
    return SimpleNamespace(
        config=cfg, physics=port, emit=lambda name, **data: events.append((name, data))
    ), events


def test_initial_and_handoff_constraints_capture_measured_state_without_pose_correction():
    ctx, events = context()
    placements = QualifiedPlacements(ctx)
    before = ctx.physics.body.copy()
    placements.initialize(ctx)
    np.testing.assert_array_equal(ctx.physics.body, before)
    assert ctx.physics.enabled == {(-1, 0), (0, 1)}
    # Hand attachment is real before the former station is removed.
    ctx.physics.weld(3, 0)
    placements.release_after_grasp(ctx, "/tube", 3)
    assert ctx.physics.changes[-2:] == [("attach", 3, 0), ("detach", -1, 0)]
    assert ctx.physics.enabled == {(3, 0), (0, 1)}
    # This is new measured feedback supplied by the external fixture, not a
    # mutation performed by placement logic. Keep its .7mm placement error.
    ctx.physics.body[0, :3] = [1.0007, 0, 0]
    before = ctx.physics.body.copy()
    placements.fix_before_open(ctx, "/tube", "/destination", {0, 1, 3}, {3}, {0, 1})
    np.testing.assert_array_equal(ctx.physics.body, before)
    assert ctx.physics.enabled == {(-1, 0), (3, 0), (0, 1)}
    event = events[-1][1]
    assert event["positionErrorM"] == pytest.approx(0.0007)
    assert event["positionLimitM"] == 0.005 and event["speedLimitMps"] == 0.01
    assert event["angleLimitRad"] == pytest.approx(np.deg2rad(5))


def test_station_cannot_release_without_actual_hand_constraint():
    ctx, _ = context()
    placements = QualifiedPlacements(ctx)
    placements.initialize(ctx)
    with pytest.raises(ValueError, match="actual hand constraint"):
        placements.release_after_grasp(ctx, "/tube", 3)
    assert (-1, 0) in ctx.physics.enabled


@pytest.mark.parametrize("failure", ["position", "angle", "speed", "occupancy", "foreign"])
def test_unqualified_placement_never_establishes_station_constraint(failure):
    ctx, events = context()
    placements = QualifiedPlacements(ctx)
    ctx.physics.weld(3, 0)
    ctx.physics.body[0, :3] = [1, 0, 0]
    if failure == "position":
        ctx.physics.body[0, 0] += 0.00501
    elif failure == "angle":
        angle = np.deg2rad(5.01)
        ctx.physics.body[0, 3:] = [0, 0, np.sin(angle / 2), np.cos(angle / 2)]
    elif failure == "speed":
        ctx.physics.velocity[0, 0] = 0.01001
    elif failure == "occupancy":
        ctx.config.tubes.append("/other")
        ctx.physics.body[2, :3] = [1, 0, 0]
    else:
        ctx.physics.contacts = [
            SimpleNamespace(bodies=(0, 2), shapes=("/tube/Floor", "/other/Wall"), distance_m=-0.0021)
        ]
    with pytest.raises(ValueError):
        placements.fix_before_open(ctx, "/tube", "/destination", {0, 3}, {3}, {0})
    assert ctx.physics.enabled == {(3, 0)}
    assert events == []


def test_initial_penetration_fails_before_any_constraint_is_enabled():
    ctx, events = context()
    ctx.physics.contacts = [SimpleNamespace(distance_m=-0.00001)]
    placements = QualifiedPlacements(ctx)
    with pytest.raises(ValueError, match="penetration"):
        placements.initialize(ctx)
    assert ctx.physics.enabled == set() and events == []


def test_open_width_alone_cannot_claim_clearance_while_finger_still_touches_attached_cap():
    ctx, _ = context()
    placements = QualifiedPlacements(ctx)
    placements.initialize(ctx)
    ctx.physics.contacts = [SimpleNamespace(bodies=(3, 1), distance_m=0.0)]
    assert not placements.fixed_and_clear(ctx, "/tube", {3})
    ctx.physics.contacts = []
    assert placements.fixed_and_clear(ctx, "/tube", {3})
