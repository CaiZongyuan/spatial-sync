"""Taught, feedback-checked FR3 motion through the indexed PhysicsPort."""

import numpy as np
from pydantic import Field, model_validator

from packages.info.program import ProgramConfig, Reject
from packages.protocol.math import rotate_vector

from .taught import require_postures
from .trajectory import plan_trajectory

HOME = (0.0, -0.4, 0.0, -2.0, 0.0, 1.6, 0.7)
VELOCITY_LIMITS = (2.62, 2.62, 2.62, 2.62, 5.26, 4.18, 5.26)


class RobotArmProgram:
    id, type, tick_priority = "robot_arm.newton", "robot-arm/v1", 10

    class Config(ProgramConfig):
        locations: dict = Field(json_schema_extra={"documentSchema": "locations"})
        tubes: list[str] = Field(json_schema_extra={"spatialReference": "body"})
        speedScale: float = Field(default=0.5, gt=0, le=1)
        graspMode: str = Field(default="weld", pattern="^(contact|weld)$")
        jointKeys: list[str] = Field(json_schema_extra={"spatialReference": "joint", "posture": True})
        fingerKeys: list[str] = Field(json_schema_extra={"spatialReference": "joint"})
        handBody: str = Field(json_schema_extra={"spatialReference": "body"})
        tcpSite: str = Field(json_schema_extra={"spatialReference": "site"})

        @model_validator(mode="after")
        def taught_poses(self):
            require_postures(self.locations, [(None, None) for _ in self.jointKeys])
            return self

    def init(self, ctx):
        port = ctx.physics
        self.joints = [port.bind_joint(key) for key in ctx.config.jointKeys]
        self.fingers = [port.bind_joint(key) for key in ctx.config.fingerKeys]
        self.hand, self.tcp = port.bind_body(ctx.config.handBody), port.bind_site(ctx.config.tcpSite)
        self.tubes = {key: port.bind_body(key) for key in ctx.config.tubes}
        self.locations = ctx.config.locations["locations"]
        self.handle, self.segment, self.plan = None, None, []
        self.transport, self.cancel_requested, self.released = False, False, None
        self.pick_tube = None
        self.pick_failure = None
        self.pending_release = set()
        self.gripper_target = self.gripper_goal = self._width(port)
        ctx.set(phase="idle", holding=None, atLocation=None, zone=None)
        self._facts(ctx)

    def _positions(self, port):
        return np.array([port.joint_q(joint)[0] for joint in self.joints])

    def _width(self, port):
        return float(sum(port.joint_q(joint)[0] for joint in self.fingers))

    def _finger_position(self, port, tube):
        p, q = port.site_pose(self.tcp)
        return rotate_vector([-q[0], -q[1], -q[2], q[3]], port.body[tube, :3] - p)

    def _in_fingers(self, port, tube):
        local = self._finger_position(port, tube)
        return abs(local[0]) < 0.016 and abs(local[1]) < 0.02 and abs(local[2]) < 0.025

    def _facts(self, ctx):
        port = ctx.physics
        width = self._width(port)
        holding = None
        for key, body in self.tubes.items():
            held = (
                port.welds.active(self.hand, body)
                if ctx.config.graspMode == "weld"
                else width < 0.025 and self._in_fingers(port, body)
            )
            if held:
                holding = key
                break
        ctx.set(
            jointPositions=self._positions(port).tolist(),
            tcpPose=np.r_[*port.site_pose(self.tcp)].tolist(),
            gripperWidth=max(0.0, min(0.08, width)),
            holding=holding,
        )

    def accept(self, inv, ctx):
        names = [inv.params[k] for k in ("location", "from", "to") if k in inv.params]
        for name in names:
            if name not in self.locations:
                return Reject(code="invalid_params", message=f"Unknown taught location {name}")
            failed = ctx.check(self.locations[name].get("requires", []))
            if failed:
                return Reject(
                    code="precondition_failed",
                    message="Taught location is unavailable",
                    details={"failed": failed},
                )
        if inv.action in ("pick", "pickPlace"):
            name = inv.params.get("from", inv.params.get("location"))
            if not any(ctx.read(tube, "location") == self.locations[name]["site"] for tube in self.tubes):
                return Reject(code="location_empty", message=f"No tube at {name}")

    def start(self, inv, ctx):
        if inv.action == "stop":
            self._hold(ctx)
            self.cancel_requested = True
            inv.succeed()
            return
        self.handle, self.segment, self.plan = inv, None, []
        self.cancel_requested, self.transport, self.released = False, False, None
        self.pick_failure = None
        action, params = inv.action, inv.params
        if action == "home":
            self.plan = [("move", HOME, None)]
        elif action == "moveJoints":
            self.plan = [("move", params["positions"], None)]
        elif action == "moveTo":
            name = params["location"]
            self.plan = [("move", self.locations[name][params.get("pose", "approach")], name)]
        elif action == "setGripper":
            self.plan = [("gripper", params["width"], None)]
        else:
            operations = (
                [("pick", params["from"]), ("place", params["to"])]
                if action == "pickPlace"
                else [(action, params["location"])]
            )
            self.pick_tube = ctx.get("holding")
            for operation, name in operations:
                location = self.locations[name]
                if operation == "pick":
                    self.pick_tube = next(
                        tube for tube in self.tubes if ctx.read(tube, "location") == location["site"]
                    )
                    self.pick_height = float(ctx.physics.body[self.tubes[self.pick_tube], 2])
                self.plan.append(("move", location["approach"], name))
                if operation == "pick":
                    self.plan.append(("gripper", 0.08, None))
                self.plan.extend(
                    [
                        ("check", None, name),
                        ("move", location["grasp"], name),
                        (operation, None, name),
                        ("move", location["approach"], name),
                        ("verify" + operation, None, name),
                    ]
                )
        self.total, self.completed = len(self.plan), 0

    def cancel(self, inv, ctx):
        self.handle = self.handle or inv
        self.cancel_requested = True

    def _end(self, ctx, failure=None, **details):
        if failure:
            self.handle.fail(*failure, **details)
        ctx.set(phase="idle")
        if self.released:
            ctx.physics.releasing.discard(self.released)
        self.handle, self.segment, self.plan = None, None, []
        self.transport = False
        self.pick_failure = None

    def _recover_pick(self, ctx, name):
        self.pick_failure = ("grasp_failed", "Gripper did not settle")
        self._open(ctx)
        self.transport, self.segment = False, None
        self.plan = [("move", self.locations[name]["approach"], name), ("failpick", None, name)]
        self.total = self.completed + len(self.plan)

    def _hold(self, ctx):
        for joint in self.joints:
            ctx.physics.set_joint_target(joint, ctx.physics.joint_q(joint))
        self.gripper_target = self.gripper_goal = self._width(ctx.physics)
        self.pending_release.clear()
        self._drive_gripper(ctx, 0)

    def _open(self, ctx):
        port = ctx.physics
        for body in self.tubes.values():
            if port.welds.active(self.hand, body):
                self.pending_release.add(body)
        self.gripper_goal = 0.08

    def _drive_gripper(self, ctx, dt):
        # URDF finger speed is .2m/s each. A position step from80mm to0
        # otherwise crosses the contact interval between control snapshots.
        self.gripper_target += float(np.clip(self.gripper_goal - self.gripper_target, -0.4 * dt, 0.4 * dt))
        for joint in self.fingers:
            ctx.physics.set_joint_target(joint, [self.gripper_target / 2])
        actual = self._width(ctx.physics)
        for body in tuple(self.pending_release):
            local = self._finger_position(ctx.physics, body)
            if actual >= 0.017 + 2 * abs(local[1]):
                ctx.physics.unweld(self.hand, body)
                self.pending_release.remove(body)

    def tick(self, ctx, dt):
        port = ctx.physics
        if ctx.fact_due:
            self._facts(ctx)
        inv = self.handle
        if inv is None:
            self._drive_gripper(ctx, dt)
            return
        if self.cancel_requested:
            self._hold(ctx)
            inv.cancelled()
            self._end(ctx)
            return
        if self.transport and ctx.fact_due and ctx.get("holding") != self.pick_tube:
            self._open(ctx)
            self._end(ctx, ("grasp_failed", "Tube left the gripper during transport"))
            return
        if self.segment is None:
            if not self.plan:
                if inv.action == "home":
                    ctx.set(atLocation=None, zone=None)
                inv.succeed()
                self._end(ctx)
                return
            self.segment = self.plan.pop(0)
            kind, target, name = self.segment
            self.elapsed, self.stable, self.last_width = 0.0, 0.0, self._width(port)
            if name:
                ctx.set(zone=self.locations[name]["zone"])
            ctx.set(phase="moving" if kind == "move" else "releasing" if kind == "place" else "grasping")
            if kind == "move":
                self.motion = plan_trajectory(
                    self._positions(port),
                    target,
                    VELOCITY_LIMITS,
                    speed_scale=ctx.config.speedScale,
                    speed=inv.params.get("speed", 1),
                )
            elif kind == "place" or kind == "gripper" and target > self._width(port) + 0.0005:
                if kind == "place" and self.pick_tube:
                    self.released = self.pick_tube
                    port.releasing.add(self.pick_tube)
                self.transport = False
                self._open(ctx)
        kind, target, name = self.segment
        self.elapsed += dt
        done = False
        if kind == "move":
            if self.pick_failure:
                self._drive_gripper(ctx, dt)
            q, _ = self.motion.sample(self.elapsed)
            for joint, value in zip(self.joints, q, strict=True):
                port.set_joint_target(joint, [value])
            done = (
                self.elapsed >= self.motion.duration
                and np.max(np.abs(self._positions(port) - target)) < 0.01
                and max(abs(port.joint_qd(j)[0]) for j in self.joints) < 0.05
            )
            if self.elapsed > 2 * self.motion.duration + 1:
                self._hold(ctx)
                self._end(
                    ctx, ("motion_timeout", "Joint feedback did not settle within the planned deadline")
                )
                return
            if done and name:
                ctx.set(atLocation=name)
        elif kind in ("gripper", "pick", "place"):
            width = 0 if kind == "pick" else 0.08 if kind == "place" else target
            self.gripper_goal = width
            self._drive_gripper(ctx, dt)
            actual = self._width(port)
            if kind == "pick" and ctx.config.graspMode == "weld":
                body = self.tubes[self.pick_tube]
                local = self._finger_position(port, body)
                # Capture at first pad contact, including measured centering
                # error; nominal opening alone can push an offset tube away.
                if actual <= 0.017 + 2 * abs(local[1]) and self._in_fingers(port, body):
                    port.weld(self.hand, body)
            self.stable = self.stable + dt if abs(actual - self.last_width) < 0.0005 else 0
            self.last_width = actual
            done = (
                self.elapsed >= 0.2 and self.stable >= 0.2 and (kind != "place" or not self.pending_release)
            )
            if self.elapsed > 2:
                if kind == "pick":
                    self._recover_pick(ctx, name)
                else:
                    self._hold(ctx)
                    code = "place_failed" if kind == "place" else "motion_timeout"
                    self._end(ctx, (code, "Gripper did not settle"))
                return
        elif kind == "check":
            failed = ctx.check(self.locations[name].get("requires", []))
            if failed:
                self._end(
                    ctx,
                    ("precondition_failed", "Taught location became unavailable before descent"),
                    failed=failed,
                )
                return
            done = True
        elif kind == "failpick":
            self._end(ctx, self.pick_failure)
            return
        elif kind == "verifypick":
            done = (
                ctx.get("holding") == self.pick_tube
                and port.body[self.tubes[self.pick_tube], 2] - self.pick_height >= 0.02
            )
            if done:
                self.transport = True
            elif self.elapsed > 0.2:
                self._open(ctx)
                self._end(
                    ctx,
                    ("grasp_failed", "Tube did not lift at least two centimetres inside the gripper"),
                    holding=ctx.get("holding"),
                    liftM=float(port.body[self.tubes[self.pick_tube], 2] - self.pick_height),
                    tubePose=port.body[self.tubes[self.pick_tube]].tolist(),
                    tcpPose=np.r_[*port.site_pose(self.tcp)].tolist(),
                )
                return
        elif kind == "verifyplace":
            done = ctx.read(self.pick_tube, "location") == self.locations[name]["site"]
            if not done and self.elapsed > 1:
                self._end(ctx, ("place_failed", "Tube did not settle at the destination Site"))
                return
        inv.progress(min(0.999, self.completed / self.total))
        if done:
            self.completed += 1
            self.segment = None
