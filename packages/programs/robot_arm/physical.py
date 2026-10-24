"""Fixed feedback and conservative zone facts shared by policy and script Programs."""

import numpy as np
from pydantic import Field

from packages.info.program import ProgramConfig

from .grasp import grasp_point_in_tcp
from .newton import HOME


class RobotArmPhysicsConfig(ProgramConfig):
    jointKeys: list[str] = Field(min_length=7, max_length=7, json_schema_extra={"spatialReference": "joint"})
    fingerKeys: list[str] = Field(min_length=2, max_length=2, json_schema_extra={"spatialReference": "joint"})
    tubes: list[str] = Field(json_schema_extra={"spatialReference": "body"})
    tcpSite: str = Field(json_schema_extra={"spatialReference": "site"})
    locations: dict = Field(json_schema_extra={"documentSchema": "locations"})
    graspHeightM: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    maxSeconds: float = Field(default=30.0, gt=0, le=600, allow_inf_nan=False)


class RobotArmPhysicsFacts:
    def _settled_location(self, ctx):
        positions = np.array([ctx.physics.joint_q(self.joints[key])[0] for key in ctx.config.jointKeys])
        speeds = np.array([ctx.physics.joint_qd(self.joints[key])[0] for key in ctx.config.jointKeys])
        if (
            not np.isfinite(positions).all()
            or not np.isfinite(speeds).all()
            or np.max(np.abs(speeds)) >= 0.05
        ):
            return {}
        # Use the existing taught Program's completion bounds, not desired targets.
        if np.max(np.abs(positions - HOME)) < 0.01:
            return {"atLocation": None, "zone": None}
        matches = {
            name: location["zone"]
            for name, location in self.locations.items()
            if any(
                np.max(np.abs(positions - location[pose])) < 0.01
                for pose in ("approach", "grasp")
                if pose in location
            )
        }
        if len(set(matches.values())) != 1:
            return {}
        facts = {"zone": next(iter(matches.values()))}
        if len(matches) == 1:
            facts["atLocation"] = next(iter(matches))
        return facts

    def _motion_zone(self, params):
        locations = [self.locations[params[key]] for key in ("location", "from", "to") if key in params]
        # Unstaged joint-space policies may visit any configured interlocked location.
        if not locations:
            locations = list(self.locations.values())
        # The model has no path constraint, even when its Task names only rack sites.
        guarded = {item["zone"] for item in self.locations.values() if item.get("requires")}
        zones = guarded or {item["zone"] for item in locations}
        if len(zones) != 1:
            raise ValueError("Policy motion requires one conservatively reservable zone")
        return next(iter(zones))

    def _facts(self, ctx):
        port = ctx.physics
        width = float(sum(port.joint_q(self.joints[key])[0] for key in ctx.config.fingerKeys))
        p, q = port.site_pose(self.tcp)
        held = []
        for key, body in self.tubes.items():
            bp, bq = port.body_pose(body)
            local = grasp_point_in_tcp(np.asarray(bp) - p, bq, q, ctx.config.graspHeightM)
            if width < 0.025 and abs(local[0]) < 0.016 and abs(local[1]) < 0.02 and abs(local[2]) < 0.025:
                held.append(key)
        ctx.set(
            jointPositions=[float(port.joint_q(self.joints[key])[0]) for key in ctx.config.jointKeys],
            gripperWidth=max(0.0, min(0.08, width)),
            tcpPose=np.r_[p, q].tolist(),
            holding=held[0] if len(held) == 1 else None,
        )
        if self.handle is None:
            # Unknown feedback leaves an old reservation (or initial UNKNOWN) intact.
            ctx.set(**self._settled_location(ctx))
