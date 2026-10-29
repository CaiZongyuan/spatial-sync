"""Carrier/tube handling over the original calibrated FR3 physical hand."""

import numpy as np
from pydantic import Field, FiniteFloat, model_validator

from packages.info.program import ProgramConfig, Reject
from packages.programs.centrifuge.loading import rotation
from packages.programs.robot_arm.newton import RobotArmProgram

from .contacts import contacts_for_bodies, validate_seating_contacts
from .facts import seated_station
from .grasp import physical_grasp


class PostureJointConfig(ProgramConfig):
    joint: str = Field(json_schema_extra={"spatialReference": "joint"})
    position: FiniteFloat


class PostureContextConfig(ProgramConfig):
    site: str = Field(json_schema_extra={"spatialReference": "site"})
    offsetM: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    joints: list[PostureJointConfig]


class LabwareMotionProgram(RobotArmProgram):
    id, type = "labware.motion", "labware-motion/v1"
    gripper_speed_mps = 0.04
    gripper_timeout_s = 3

    class Config(ProgramConfig):
        locations: dict = Field(json_schema_extra={"documentSchema": "locations"})
        postureContexts: dict[str, PostureContextConfig] = Field(
            default_factory=dict, json_schema_extra={"postureContext": True}
        )
        tubes: list[str] = Field(json_schema_extra={"spatialReference": "body"})
        speedScale: float = Field(default=0.3, gt=0, le=1)
        automaticOpenWidthM: float = Field(default=.08, gt=0, le=.08, allow_inf_nan=False)
        gripperSpeedMPerS: float = Field(default=.04, gt=0, le=.04, allow_inf_nan=False)
        contactAllowanceM: float = Field(default=.001, gt=0, le=.002, allow_inf_nan=False)
        seatingContactPairs: list[tuple[str, str]] = Field(default_factory=list, max_length=8)
        seatingContactAllowanceM: float = Field(default=.002, gt=0, le=.005, allow_inf_nan=False)
        jointAccelerationRadPerS2: float = Field(default=2., gt=0, le=2., allow_inf_nan=False)
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
        gripShapePrefixes: dict[str, str]
        fingerPadShapes: list[str] = Field(min_length=2, max_length=2)
        attachedLids: dict[str, str] = Field(default_factory=dict)
        symmetricPreInsertionLocations: list[str] = Field(default_factory=list)

        @model_validator(mode="after")
        def calibrated_grips(self):
            from packages.programs.robot_arm.taught import require_postures

            require_postures(self.locations, [(None, None)] * 7)
            validate_seating_contacts(self.seatingContactPairs, self.seatingContactAllowanceM)
            for name in self.symmetricPreInsertionLocations:
                if "preInsertion" not in self.locations["locations"].get(name, {}):
                    raise ValueError("Symmetric insertion requires a taught preInsertion posture")
            if (
                len(set(self.tubes)) != len(self.tubes)
                or len(set(self.jointKeys)) != 7
                or len(set(self.fingerKeys)) != 2
                or set(self.graspOffsets) != set(self.tubes)
                or set(self.gripDiameters) != set(self.tubes)
                or set(self.gripShapePrefixes) != set(self.tubes)
            ):
                raise ValueError("Every labware body needs an explicit physical grip")
            for key in self.tubes:
                offset, diameter = self.graspOffsets[key], self.gripDiameters[key]
                if len(offset) != 3 or not np.isfinite(offset).all() or not 0 < diameter <= 0.078:
                    raise ValueError("Grip offsets must be finite triples and fit the 80 mm hand")
                if diameter >= self.automaticOpenWidthM:
                    raise ValueError("Every grip must fit inside the configured automatic opening")
                if not self.gripShapePrefixes[key].startswith(key + "/"):
                    raise ValueError("Grip surface must name a shape beneath its physical body")
            return self

    def init(self, ctx):
        self.gripper_open_width_m = ctx.config.automaticOpenWidthM
        self.gripper_speed_mps = ctx.config.gripperSpeedMPerS
        self.contact_allowance_m = ctx.config.contactAllowanceM
        self.joint_acceleration = ctx.config.jointAccelerationRadPerS2
        self.symmetric_pre_insertion = set(ctx.config.symmetricPreInsertionLocations)
        self.body_grips = {
            ctx.physics.bind_body(key): (ctx.config.graspOffsets[key], ctx.config.gripDiameters[key])
            for key in ctx.config.tubes
        }
        self.control_entity = ctx.config.controlEntity
        self.carrying_lid = None
        self.motion_payload = None
        self.grip_hold_width = None
        self.pad_shapes = {
            ctx.physics.bind_joint(key).child: shape
            for key, shape in zip(ctx.config.fingerKeys, ctx.config.fingerPadShapes, strict=True)
        }
        self.grip_prefixes = {
            ctx.physics.bind_body(key): ctx.config.gripShapePrefixes[key] for key in ctx.config.tubes
        }
        super().init(ctx)
        ctx.set(unsafeContactCount=0, lastContactShapes=[], lastContactDepthM=0.0)

    def _finger_position(self, port, body):
        point, orientation = port.site_pose(self.tcp)
        pose = port.body[body]
        offset, _ = self.body_grips[body]
        return rotation(orientation).T @ (pose[:3] + rotation(pose[3:]) @ offset - point)

    def _in_fingers(self, port, body):
        return self._grasp_ready(port, body, self._width(port))

    def _grasp_ready(self, port, body, opening):
        offset, diameter = self.body_grips[body]
        try:
            contacts = port.collision_contacts(bodies={body})
        except ValueError:
            return False  # Unavailable physics cannot qualify a fixed grasp.
        qualified = physical_grasp(
            port.body[body],
            np.r_[*port.site_pose(self.tcp)],
            offset,
            diameter,
            opening,
            contacts,
            body=body,
            fingers={finger.child for finger in self.fingers},
            pad_shapes=self.pad_shapes,
            shape_prefix=self.grip_prefixes[body],
            contact_allowance_m=self.contact_allowance_m,
        )
        if qualified:
            self.grip_hold_width = opening
        return qualified

    def _drive_gripper(self, ctx, dt):
        if (
            self.grip_hold_width is not None
            and self.gripper_goal < self.grip_hold_width
            and any(ctx.physics.welds.active(self.hand, body) for body in self.tubes.values())
        ):
            self.gripper_goal = self.gripper_target = self.grip_hold_width
        super()._drive_gripper(ctx, dt)

    def _release_opening(self, port, body):
        return self.body_grips[body][1] + 2 * abs(self._finger_position(port, body)[1]) + 0.003

    def _gripper_settled(self, ctx, kind, actual, dt):
        stationary = dt > 0 and abs(actual - self.last_width) <= min(.002, self.gripper_speed_mps * .1) * dt
        if kind == "pick":
            return (stationary and self.pick_tube in self.tubes
                    and ctx.physics.welds.active(self.hand, self.tubes[self.pick_tube]))
        return stationary and abs(actual - self.gripper_goal) < .001

    def _gripper_budget(self, start, goal):
        return max(self.gripper_timeout_s, abs(goal - start) / self.gripper_speed_mps + .5)

    def _facts(self, ctx):
        super()._facts(ctx)
        safe = (
            self.handle is None
            and max(abs(ctx.physics.joint_qd(j)[0]) for j in self.joints) < 0.02
            and max(abs(ctx.physics.joint_qd(j)[0]) for j in self.fingers) < 0.01
            and np.linalg.norm(ctx.physics.velocity[self.hand]) < 0.01
        )
        holding = ctx.get("holding")
        if holding:
            safe = safe and np.linalg.norm(ctx.physics.velocity[self.tubes[holding]]) < 0.01
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
        failed = self._guard(ctx, inv.params) or super().accept(inv, ctx)
        if failed or inv.action not in ("place", "pickPlace"):
            return failed
        name = inv.params["to"] if inv.action == "pickPlace" else inv.params["location"]
        site = self.locations[name]["site"]
        moving = (
            next(
                (
                    key
                    for key in self.tubes
                    if ctx.read(key, "location") == self.locations[inv.params["from"]]["site"]
                ),
                None,
            )
            if inv.action == "pickPlace"
            else ctx.get("holding")
        )
        occupied = [key for key in self.tubes if key != moving and ctx.read(key, "location") == site]
        if occupied:
            return Reject(
                code="station_occupied",
                message="The actual target station is occupied",
                details={"site": site, "occupants": occupied},
            )

    def start(self, inv, ctx):
        failed = self.accept(inv, ctx)
        if failed:
            inv.fail(failed.code, failed.message)
            return
        ctx.set(controlSafe=False)
        super().start(inv, ctx)
        if inv.action in ("pick", "place", "pickPlace"):
            staged = []
            for index, segment in enumerate(self.plan):
                kind, _, name = segment
                if (kind == "move" and name and index + 1 < len(self.plan)
                        and (self.plan[index + 1][0] == "place"
                             or self.plan[index + 1][0] == "pick" and name in self.symmetric_pre_insertion)
                        and "preInsertion" in self.locations[name]):
                    staged.append(("move", self.locations[name]["preInsertion"], name))
                staged.append(segment)
                if kind in ("pick", "place") and name in self.symmetric_pre_insertion:
                    staged.append(("move", self.locations[name]["preInsertion"], name))
            self.plan, self.total = staged, len(staged)
        if inv.action != "stop":
            ctx.set(unsafeContactCount=0, lastContactShapes=[], lastContactDepthM=0.0)
        body = (
            ctx.get("holding") or self.pick_tube
            if inv.action in ("pick", "place", "pickPlace")
            else ctx.get("holding")
        )
        self.motion_payload = body
        self.carrying_lid = (
            ctx.config.attachedLids.get(body) if body and ctx.read(body, "lidClosed") is True else None
        )

    def _preview_targets(self, action, params):
        if action not in ("pick", "place", "pickPlace"):
            return super()._preview_targets(action, params)
        operations = (("pick", params["from"]), ("place", params["to"])) if action == "pickPlace" else (
            (action, params["location"]),)
        targets = []
        for operation, name in operations:
            location = self.locations[name]
            middle = [location["preInsertion"]] if "preInsertion" in location else []
            symmetric = name in self.symmetric_pre_insertion
            incoming = middle if operation == "place" or symmetric else []
            outgoing = middle if symmetric else []
            targets.extend([
                location["approach"], *incoming, location["grasp"], *outgoing, location["approach"],
            ])
        return targets

    def _contact_guard(self, ctx):
        active = {joint.child for joint in [*self.joints, *self.fingers] if joint.child >= 0} | {self.hand}
        for key in (ctx.get("holding"), self.motion_payload, self.released, self.carrying_lid):
            if key in self.tubes:
                active.add(self.tubes[key])
        try:
            contacts = contacts_for_bodies(
                ctx.physics, active, limit_m=self.contact_allowance_m,
                seating_pairs=ctx.config.seatingContactPairs,
                seating_limit_m=ctx.config.seatingContactAllowanceM,
                on_seating_contact=(lambda **data: ctx.emit("seatingContact", **data))
                if ctx.config.seatingContactPairs else None,
            )
        except ValueError:
            self._hold(ctx)
            self._end(ctx, ("contact_unavailable", "Actual motion contact facts are unavailable"))
            return False
        hit = contacts[0] if contacts else None
        if hit is not None:
            self._hold(ctx)
            shapes, depth = list(hit.shapes), -hit.distance_m
            ctx.set(
                unsafeContactCount=ctx.get("unsafeContactCount") + 1,
                lastContactShapes=shapes,
                lastContactDepthM=depth,
            )
            ctx.emit("unsafeContact", shapes=shapes, depthM=depth)
            self._end(
                ctx,
                ("unsafe_contact", "Actual arm or payload contact exceeded scene allowance"),
                shapes=shapes,
                depthM=depth,
            )
            return False
        return True

    def tick(self, ctx, dt):
        if self.handle is not None:
            if not self._contact_guard(ctx):
                return
            failed = self._guard(ctx, self.handle.params)
            if failed:
                self._hold(ctx)
                self._end(ctx, (failed.code, failed.message))
        super().tick(ctx, dt)
