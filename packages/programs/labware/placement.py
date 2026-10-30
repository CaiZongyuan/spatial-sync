"""Opt-in measured constraint handoff. No body or joint pose is ever written."""

import numpy as np

from packages.programs.centrifuge.loading import rotation

from .contacts import contacts_for_bodies
from .facts import (
    SEATED_ANGLE_LIMIT_RAD,
    SEATED_POSITION_LIMIT_M,
    SEATED_SPEED_LIMIT_MPS,
    seated_station,
)


class QualifiedPlacements:
    def __init__(self, ctx):
        self.stations = ctx.config.placementStations
        self.anchors = {}
        for key, stations in self.stations.items():
            if not stations or len(stations) != len(set(stations)) or key not in ctx.config.tubes:
                raise ValueError("Every placement declaration needs known objects and unique stations")
            ctx.physics.bind_body(key)
            for site in stations:
                if ctx.physics.bind_site(site).body == ctx.physics.bind_body(key):
                    raise ValueError("An object cannot be anchored to its own Site")

    def measurements(self, ctx, key, site):
        port = ctx.physics
        body = port.bind_body(key)
        refs = port.bind_sites([site])
        pose, target = port.body[body], port.site_poses(refs)[0]
        velocity = np.asarray(port.relative_site_velocity(body, refs), dtype=float)
        if not np.isfinite(np.r_[pose, target, velocity.ravel()]).all():
            raise ValueError("Placement observations must be finite")
        aligned = rotation(target[3:]).T @ rotation(pose[3:])
        evidence = {
            "object": key,
            "station": site,
            "positionErrorM": float(np.linalg.norm(pose[:3] - target[:3])),
            "angleErrorRad": float(np.arccos(np.clip((np.trace(aligned) - 1) / 2, -1, 1))),
            "relativeSpeedMps": float(np.linalg.norm(velocity[0])),
            "positionLimitM": SEATED_POSITION_LIMIT_M,
            "angleLimitRad": float(SEATED_ANGLE_LIMIT_RAD),
            "speedLimitMps": SEATED_SPEED_LIMIT_MPS,
        }
        valid = seated_station(pose, velocity, [target], [site]) == site
        return valid, evidence

    def _unoccupied(self, ctx, key, site):
        for other in ctx.config.tubes:
            if other == key:
                continue
            if self.anchors.get(other) == site:
                raise ValueError("Placement station is already constrained to another object")
            valid, _ = self.measurements(ctx, other, site)
            if valid:
                raise ValueError("Placement station is physically occupied")

    def _emit(self, ctx, operation, evidence):
        ctx.emit("constraintChanged", operation=operation, **evidence)

    def initialize(self, ctx):
        # Validate every initial placement before enabling any of them.
        evidence = []
        for key, site in ctx.config.initialPlacements.items():
            if site not in self.stations.get(key, []):
                raise ValueError("Initial placement must be explicitly declared")
            valid, measured = self.measurements(ctx, key, site)
            if not valid:
                raise ValueError("Initial placement does not satisfy the existing seated_station limits")
            self._unoccupied(ctx, key, site)
            contacts = ctx.physics.collision_contacts(max_distance=0.0, bodies={ctx.physics.bind_body(key)})
            penetrating = [c for c in contacts if c.distance_m < 0]
            if penetrating:
                worst = min(penetrating, key=lambda c: c.distance_m)
                raise ValueError(
                    f"Initial placement has actual penetration: {key} at {site}, "
                    f"shapes={worst.shapes}, depthM={-worst.distance_m}"
                )
            evidence.append((key, site, measured))
        for key, site, measured in evidence:
            ctx.physics.weld(ctx.physics.bind_site(site).body, ctx.physics.bind_body(key))
            self.anchors[key] = site
            self._emit(ctx, "initialize", measured)

    def fix_before_open(self, ctx, key, site, moving_scope, fingers, grasp_bodies):
        if site not in self.stations.get(key, []):
            raise ValueError("Undeclared placement station")
        valid, evidence = self.measurements(ctx, key, site)
        if not valid:
            raise ValueError("Actual placement does not satisfy seated_station limits")
        self._unoccupied(ctx, key, site)
        if contacts_for_bodies(
            ctx.physics,
            moving_scope,
            limit_m=ctx.config.contactAllowanceM,
            expected_pairs=ctx.config.expectedContactPairs,
            finger_bodies=fingers,
            grasp_bodies=grasp_bodies,
        ):
            raise ValueError("Unexpected contact prevents placement")
        if key in self.anchors:
            raise ValueError("Object still has its previous station constraint")
        ctx.physics.weld(ctx.physics.bind_site(site).body, ctx.physics.bind_body(key))
        self.anchors[key] = site
        self._emit(ctx, "attach", evidence)
        return evidence

    def release_after_grasp(self, ctx, key, hand):
        if not ctx.physics.welds.active(hand, ctx.physics.bind_body(key)):
            raise ValueError("Station cannot release before the actual hand constraint is active")
        site = self.anchors.get(key)
        if site is None:
            return
        _, evidence = self.measurements(ctx, key, site)
        ctx.physics.unweld(ctx.physics.bind_site(site).body, ctx.physics.bind_body(key))
        del self.anchors[key]
        self._emit(ctx, "detach", evidence)

    def fixed_and_clear(self, ctx, key, fingers):
        site = self.anchors.get(key)
        if site is None:
            return False
        port = ctx.physics
        body = port.bind_body(key)
        if not port.welds.active(port.bind_site(site).body, body):
            return False
        assembly = port.welds.attached_bodies({body})
        contacts = port.collision_contacts(max_distance=0.0, bodies=fingers)
        return not any(
            set(row.bodies) & fingers and set(row.bodies) & assembly and row.distance_m <= 0
            for row in contacts
        )
