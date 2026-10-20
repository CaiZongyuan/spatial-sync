"""Taught virtual arm; displayed links and TCP use this same kinematic model."""

import math

from pydantic import Field, model_validator

from packages.info.program import ProgramConfig, Reject
from packages.protocol.math import compose_pose

from .preview import joint_targets, path_preview, taught_waypoints
from .taught import require_postures
from .trajectory import plan_trajectory

HOME = [0.0, 0.3, -0.6, 0.0, 0.0, 0.0, 0.0]
TAUGHT_LIMITS = tuple((None, None) for _ in HOME)
BASE_HEIGHT = 0.9
VELOCITY_LIMITS = (1.0,) * len(HOME)


def kinematics(joints):
    yaw, shoulder, elbow, *wrist = joints
    origin = {"p": [0, 0, BASE_HEIGHT], "q": [0, 0, math.sin(yaw / 2), math.cos(yaw / 2)]}
    first = compose_pose(
        origin, {"p": [0, 0, 0], "q": [0, math.sin(shoulder / 2), 0, math.cos(shoulder / 2)]}
    )
    second = compose_pose(first, {"p": [0, 0, 0.6], "q": [0, math.sin(elbow / 2), 0, math.cos(elbow / 2)]})
    hand = compose_pose(second, {"p": [0, 0, 0.5], "q": [0, 0, 0, 1]})
    for axis, angle in zip([0, 1, 0, 2], wrist, strict=True):
        q = [0, 0, 0, math.cos(angle / 2)]
        q[axis] = math.sin(angle / 2)
        hand = compose_pose(hand, {"p": [0, 0, 0], "q": q})
    return first, second, hand


class RobotArmProgram:
    id, type, tick_priority = "robot_arm.virtual", "robot-arm/v1", 10
    taught_limits = TAUGHT_LIMITS

    class Config(ProgramConfig):
        locations: dict = Field(json_schema_extra={"documentSchema": "locations"})
        tubes: list[str]
        speedScale: float = Field(default=1.0, gt=0, le=1)

        @model_validator(mode="after")
        def taught_poses(self):
            require_postures(self.locations, TAUGHT_LIMITS)
            return self

    def init(self, ctx):
        self.handle, self.plan, self.segment, self.cancel_requested = None, [], None, False
        self.elapsed = 0.0
        self._set_joints(ctx, HOME)
        ctx.set(phase="idle", gripperWidth=0.08, holding=None, atLocation=None, zone=None)

    def _locations(self, ctx):
        return ctx.config.locations["locations"]

    def _set_joints(self, ctx, joints):
        hand = kinematics(joints)[2]
        ctx.set(jointPositions=joints, tcpPose=[*hand["p"], *hand["q"]])

    def accept(self, inv, ctx):
        locations = self._locations(ctx)
        names = [inv.params[k] for k in ("location", "from", "to") if k in inv.params]
        for name in names:
            if name not in locations:
                return Reject(code="invalid_params", message=f"Unknown taught location {name}")
            failed = ctx.check(locations[name].get("requires", []))
            if failed:
                return Reject(
                    code="precondition_failed",
                    message="Location requires an available device",
                    details={"failed": failed},
                )
        if inv.action in ("pick", "pickPlace"):
            name = inv.params.get("from", inv.params.get("location"))
            if not any(ctx.read(tube, "location") == locations[name]["site"] for tube in ctx.config.tubes):
                return Reject(code="location_empty", message=f"No tube at {name}")

    def preview(self, action, params, ctx):
        targets = joint_targets(action, params, self._locations(ctx), HOME)
        if targets is None:
            return None
        start = ctx.values["jointPositions"]
        return ctx.cached(tuple(start), lambda: path_preview(
            start, targets, VELOCITY_LIMITS, lambda joints: kinematics(joints)[2],
            speed_scale=ctx.config.speedScale, speed=params.get("speed", 1), minimum_duration=0.2,
        ))

    def start(self, inv, ctx):
        if inv.action == "stop":
            self.cancel_requested = True
            inv.succeed()
            return
        self.handle, self.plan, self.segment, self.cancel_requested = inv, [], None, False
        params, action = inv.params, inv.action
        if action == "home":
            self.plan = [("move", HOME, None)]
        elif action == "moveJoints":
            self.plan = [("move", params["positions"], None)]
        elif action == "moveTo":
            name = params["location"]
            self.plan = [("move", self._locations(ctx)[name][params.get("pose", "approach")], name)]
        elif action == "setGripper":
            self.plan = [("gripper", params["width"], None)]
        else:
            operations = (
                [("pick", params["from"]), ("place", params["to"])]
                if action == "pickPlace"
                else [(action, params["location"])]
            )
            for operation, name in operations:
                location = self._locations(ctx)[name]
                approach, grasp, retreat = taught_waypoints(location)
                self.plan.extend(
                    [
                        ("move", approach, name),
                        ("gripper", 0.08 if operation == "pick" else 0.016, None),
                        ("check", None, name),
                        ("move", grasp, name),
                        (operation, None, name),
                        ("move", retreat, name),
                    ]
                )
        self.total_segments = len(self.plan)
        self.completed = 0

    def cancel(self, inv, ctx):
        if self.handle is None:
            self.handle = inv
        self.cancel_requested = True

    def tick(self, ctx, dt):
        inv = self.handle
        if inv is None:
            return
        if self.cancel_requested:
            ctx.set(phase="idle")
            inv.cancelled()
            self.handle, self.plan, self.segment = None, [], None
            return
        if self.segment is None:
            if not self.plan:
                ctx.set(phase="idle")
                if inv.action == "home":
                    ctx.set(atLocation=None, zone=None)
                inv.succeed()
                self.handle = None
                return
            kind, target, name = self.plan.pop(0)
            self.segment = kind, target, name
            self.elapsed = 0.0
            self.initial = ctx.get("jointPositions")
            if kind == "move":
                self.motion = plan_trajectory(self.initial, target, VELOCITY_LIMITS,
                                              speed_scale=ctx.config.speedScale,
                                              speed=inv.params.get("speed", 1))
                self.duration = max(0.2, self.motion.duration)
            else:
                self.duration = 0.2
            if name:
                ctx.set(zone=self._locations(ctx)[name]["zone"])
            ctx.set(phase="moving" if kind == "move" else "releasing" if kind == "place" else "grasping")
        kind, target, name = self.segment
        self.elapsed += dt
        fraction = min(1.0, self.elapsed / self.duration)
        if kind == "move":
            self._set_joints(ctx, self.motion.sample(self.elapsed)[0].tolist())
        if fraction + 1e-9 < 1:
            inv.progress((self.completed + fraction) / self.total_segments)
            return
        if kind == "move" and name:
            ctx.set(atLocation=name)
        elif kind == "gripper":
            ctx.set(gripperWidth=target)
        elif kind == "check":
            failed = ctx.check(self._locations(ctx)[name].get("requires", []))
            if failed:
                inv.fail("precondition_failed", "Location became unavailable before grasp", failed=failed)
                self.handle, self.plan, self.segment = None, [], None
                ctx.set(phase="idle")
                return
        elif kind == "pick":
            site = self._locations(ctx)[name]["site"]
            tube = next((tube for tube in ctx.config.tubes if ctx.read(tube, "location") == site), None)
            if tube is None:
                inv.fail("grasp_failed", "Tube is no longer at the taught position")
                self.handle = None
                ctx.set(phase="idle")
                return
            ctx.set(holding=tube, gripperWidth=0.016)
        elif kind == "place":
            ctx.set(holding=None, gripperWidth=0.08, atLocation=name)
        self.completed += 1
        self.segment = None
