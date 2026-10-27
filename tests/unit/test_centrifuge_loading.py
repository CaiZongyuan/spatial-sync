"""Loading safety must use measured bodies, independently of symbolic occupancy."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from packages.programs.centrifuge.newton import CentrifugeProgram

SITES = [f"slot{i}" for i in range(4)]
POSITIONS = np.array([[.15, 0, .02], [0, .15, .02], [-.15, 0, .02], [0, -.15, .02]])


class LoadingPort:
    def __init__(self):
        self.body = np.array([[0, 0, 0, 0, 0, 0, 1], *[
            [1+i*.03, 0, .02, 0, 0, 0, 1] for i in range(4)
        ]], dtype=float)
        self.velocities = np.zeros((4, 3))
        self.welded = []

    def bind_joint(self, key):
        return key

    def bind_site(self, key):
        return SimpleNamespace(body=0, index=SITES.index(key))

    def bind_sites(self, keys):
        return tuple(self.bind_site(key) for key in keys)

    def bind_body(self, key):
        return int(key[-1])+1

    def collision_bounds(self, body):
        return np.array([[-.19, -.19, 0], [.19, .19, .05]] if body == 0 else
                        [[-.009, -.009, 0], [.009, .009, .13]])

    def site_poses(self, sites):
        return np.column_stack((POSITIONS, np.tile([0, 0, 0, 1], (4, 1))))

    def relative_site_velocity(self, body, sites):
        return np.broadcast_to(self.velocities[body-1], (4, 3))

    def joint_q(self, joint):
        return [0]

    def weld(self, rotor, tube):
        self.welded.append((rotor, tube))


class LoadingContext:
    def __init__(self):
        self.physics = LoadingPort()
        self.config = CentrifugeProgram.Config(slotSites=SITES, tubes=[f"tube{i}" for i in range(4)],
                                               spindleJoint="spindle", lidJoint="lid")
        self.values = {}
        self.locations = {key: "rack" for key in self.config.tubes}
        self.fact_due = True

    def set(self, **values):
        self.values.update(values)

    def get(self, name):
        return self.values[name]

    def read(self, key, name):
        return self.locations[key]


def setup():
    ctx, program = LoadingContext(), CentrifugeProgram()
    program.init(ctx)
    return program, ctx


def seat(ctx, tube, slot, *, tilt=0):
    ctx.physics.body[tube+1] = [*POSITIONS[slot], 0, math.sin(math.radians(tilt)/2), 0,
                                math.cos(math.radians(tilt)/2)]
    ctx.locations[f"tube{tube}"] = SITES[slot] if tilt < 20 else "loose"


def request():
    return SimpleNamespace(action="start", params={"rpm": 3000, "durationSeconds": 10})


@pytest.mark.parametrize("slots,code", [([], None), ([0], "imbalanced"), ([0, 2], None),
                                        ([0, 1], "imbalanced"), ([0, 1, 2, 3], None)])
def test_valid_seating_preserves_the_existing_balance_rule(slots, code):
    program, ctx = setup()
    for tube, slot in enumerate(slots):
        seat(ctx, tube, slot)
    result = program.accept(request(), ctx)
    assert (result.code if result else None) == code


@pytest.mark.parametrize("failure", ["tilted", "slipping", "duplicate", "lying", "invalid", "held"])
def test_unsafe_physical_load_cannot_disappear_into_an_empty_or_balanced_rotor(failure):
    program, ctx = setup()
    seat(ctx, 0, 0, tilt=23.8 if failure == "tilted" else 0)
    if failure == "slipping":
        ctx.physics.velocities[0] = [.06, 0, 0]
    elif failure == "duplicate":
        seat(ctx, 1, 0)
    elif failure == "lying":
        # Its bottom is outside the rotor, but the horizontal shaft crosses it.
        ctx.physics.body[1] = [.25, 0, .03, 0, -math.sqrt(.5), 0, math.sqrt(.5)]
    elif failure == "invalid":
        ctx.physics.body[1, 3:] = 0
    elif failure == "held":
        ctx.locations["tube0"] = "held"
    result = program.accept(request(), ctx)
    assert result is not None and result.code == "unsafe_load"
    assert result.details["tubes"]


def test_loose_body_well_outside_the_rotor_does_not_disable_empty_runs():
    program, ctx = setup()
    ctx.locations["tube0"] = "loose"
    assert program.accept(request(), ctx) is None


def test_current_physical_load_overrules_stale_location_and_is_rechecked_before_welding():
    program, ctx = setup()
    seat(ctx, 0, 0)
    seat(ctx, 1, 2)
    assert program.accept(request(), ctx) is None
    seat(ctx, 0, 0, tilt=24)
    ctx.locations["tube0"] = SITES[0]  # factHz has not published the new tilt yet.
    failures = []
    inv = request()
    inv.fail = lambda code, message, **details: failures.append((code, details))
    program.start(inv, ctx)
    assert failures and failures[0][0] == "unsafe_load"
    assert ctx.physics.welded == []
    assert ctx.get("phase") == "idle"


def test_load_safety_fact_recovers_only_when_actual_loading_is_safe():
    program, ctx = setup()
    seat(ctx, 0, 0, tilt=24)
    program.tick(ctx, .1)
    assert ctx.get("loadSafe") is False
    assert ctx.get("slots") == [None]*4
    ctx.physics.body[1, :3] = [1, 0, .02]
    program.tick(ctx, .1)
    assert ctx.get("loadSafe") is True


def test_failed_site_pose_composition_replaces_the_previous_safe_fact():
    program, ctx = setup()
    assert ctx.get("loadSafe") is True

    def unavailable(sites):
        raise ValueError("quaternion cannot have zero length")

    ctx.physics.site_poses = unavailable
    program.tick(ctx, .1)
    assert ctx.get("loadSafe") is False
    assert program.accept(request(), ctx).code == "unsafe_load"
