"""Actual labware seating and separate physical lid alignment."""

import numpy as np
from pydantic import Field

from packages.info.program import ProgramConfig
from packages.programs.centrifuge.loading import rotation

SEATED_POSITION_LIMIT_M = 0.005
SEATED_ANGLE_LIMIT_RAD = np.deg2rad(5)
SEATED_SPEED_LIMIT_MPS = 0.01


def seated_station(pose, velocities, stations, keys, *, held=False):
    if held:
        return None
    try:
        pose, velocities, stations = (
            np.asarray(value, dtype=float) for value in (pose, velocities, stations)
        )
        if (
            not np.isfinite(pose).all()
            or not np.isfinite(velocities).all()
            or not np.isfinite(stations).all()
        ):
            return None
        body_rotation = rotation(pose[3:])
        for key, station, velocity in zip(keys, stations, velocities, strict=True):
            orientation = rotation(station[3:]).T @ body_rotation
            aligned = (np.trace(orientation) - 1) / 2 >= np.cos(SEATED_ANGLE_LIMIT_RAD)
            if (
                np.linalg.norm(pose[:3] - station[:3]) < SEATED_POSITION_LIMIT_M
                and np.linalg.norm(velocity) < SEATED_SPEED_LIMIT_MPS
                and aligned
            ):
                return key
    except (ValueError, TypeError, IndexError):
        pass
    return None


class LabwareFactsProgram:
    id, type, tick_priority = "labware.facts", "labware/v1", 20

    class Config(ProgramConfig):
        robots: list[str]
        locationSites: list[str] = Field(json_schema_extra={"spatialReference": "site"})
        lidBody: str | None = Field(
            default=None,
            exclude_if=lambda value: value is None,
            json_schema_extra={"spatialReference": "body"},
        )
        lidOffsetM: float = Field(default=0.018, gt=0, allow_inf_nan=False)

    def init(self, ctx):
        port = ctx.physics
        self.body = port.bind_body(ctx.key)
        self.sites = port.bind_sites(ctx.config.locationSites)
        self.lid = port.bind_body(ctx.config.lidBody) if ctx.config.lidBody else None
        self._publish(ctx)

    def _publish(self, ctx):
        port = ctx.physics
        held = any(ctx.read(robot, "holding") == ctx.key for robot in ctx.config.robots)
        station = seated_station(
            port.body[self.body],
            port.relative_site_velocity(self.body, self.sites),
            port.site_poses(self.sites),
            ctx.config.locationSites,
            held=held,
        )
        closed = False
        if self.lid is not None:
            pose = port.body[self.body]
            expected = pose.copy()
            expected[:3] += rotation(pose[3:]) @ [0, 0, ctx.config.lidOffsetM]
            lid_held = any(ctx.read(robot, "holding") == ctx.config.lidBody for robot in ctx.config.robots)
            relative = port.velocity[self.lid] - port.velocity[self.body]
            closed = (
                not lid_held and seated_station(port.body[self.lid], [relative], [expected], ["lid"]) == "lid"
            )
        ctx.set(
            station=station,
            location="held" if held else station or "loose",
            settled=station is not None,
            lidClosed=bool(closed),
        )

    def tick(self, ctx, dt):
        if ctx.fact_due:
            self._publish(ctx)
