"""Carrier/tube handling over the original calibrated FR3 physical hand."""

import numpy as np
from pydantic import Field, model_validator

from packages.info.program import ProgramConfig
from packages.programs.centrifuge.loading import rotation
from packages.programs.robot_arm.newton import RobotArmProgram

from .facts import seated_station
from .grasp import grasp_geometry


class LabwareMotionProgram(RobotArmProgram):
    id, type = "labware.motion", "labware-motion/v1"

    class Config(ProgramConfig):
        locations: dict = Field(json_schema_extra={"documentSchema": "locations"})
        tubes: list[str] = Field(json_schema_extra={"spatialReference": "body"})
        speedScale: float = Field(default=0.3, gt=0, le=1)
        graspMode: str = Field(default="weld", pattern="^weld$")
        jointKeys: list[str] = Field(
            min_length=7, max_length=7, json_schema_extra={"spatialReference": "joint", "posture": True}
        )
        fingerKeys: list[str] = Field(
            min_length=2, max_length=2, json_schema_extra={"spatialReference": "joint"}
        )
        handBody: str = Field(json_schema_extra={"spatialReference": "body"})
        tcpSite: str = Field(json_schema_extra={"spatialReference": "site"})
        controlEntity: str
        graspOffsets: dict[str, list[float]]
        gripDiameters: dict[str, float]

        @model_validator(mode="after")
        def calibrated_grips(self):
            from packages.programs.robot_arm.taught import require_postures

            require_postures(self.locations, [(None, None)] * 7)
            if set(self.graspOffsets) != set(self.tubes) or set(self.gripDiameters) != set(self.tubes):
                raise ValueError("Every labware body needs an explicit physical grip")
            for key in self.tubes:
                offset, diameter = self.graspOffsets[key], self.gripDiameters[key]
                if len(offset) != 3 or not np.isfinite(offset).all() or not 0 < diameter <= 0.078:
                    raise ValueError("Grip offsets must be finite triples and fit the 80 mm hand")
            return self

    def init(self, ctx):
        self.body_grips = {
            ctx.physics.bind_body(key): (ctx.config.graspOffsets[key], ctx.config.gripDiameters[key])
            for key in ctx.config.tubes
        }
        self.control_entity = ctx.config.controlEntity
        super().init(ctx)

    def _finger_position(self, port, body):
        point, orientation = port.site_pose(self.tcp)
        pose = port.body[body]
        offset, _ = self.body_grips[body]
        return rotation(orientation).T @ (pose[:3] + rotation(pose[3:]) @ offset - point)

    def _in_fingers(self, port, body):
        return self._grasp_ready(port, body, self._width(port))

    def _grasp_ready(self, port, body, opening):
        offset, diameter = self.body_grips[body]
        return grasp_geometry(port.body[body], np.r_[*port.site_pose(self.tcp)], offset, diameter, opening)

    def _release_opening(self, port, body):
        return self.body_grips[body][1] + 2 * abs(self._finger_position(port, body)[1]) + 0.003

    def _facts(self, ctx):
        super()._facts(ctx)
        safe = self.handle is None and max(abs(ctx.physics.joint_qd(j)[0]) for j in self.joints) < 0.02
        ctx.set(controlSafe=bool(safe))

    def _placement(self, port, body, sites, key):
        return (
            seated_station(
                port.body[body], port.relative_site_velocity(body, sites), port.site_poses(sites), [key]
            )
            == key
        )

    def _resources(self, ctx, params):
        sites = [
            self.locations[params[key]]["site"]
            for key in ("location", "from", "to")
            if key in params and params[key] in self.locations
        ]
        objects = [
            key for key in self.tubes if ctx.read(key, "location") in sites or ctx.get("holding") == key
        ]
        return [*sites, *objects]

    def _guard(self, ctx, params):
        from packages.programs.cell_passage.guard import guard

        return guard(ctx, self.control_entity, params, self._resources(ctx, params))

    def accept(self, inv, ctx):
        return self._guard(ctx, inv.params) or super().accept(inv, ctx)

    def start(self, inv, ctx):
        failed = self._guard(ctx, inv.params)
        if failed:
            inv.fail(failed.code, failed.message)
            return
        ctx.set(controlSafe=False)
        super().start(inv, ctx)

    def tick(self, ctx, dt):
        if self.handle is not None:
            failed = self._guard(ctx, self.handle.params)
            if failed:
                self._hold(ctx)
                self._end(ctx, (failed.code, failed.message))
        super().tick(ctx, dt)
