"""Owned scalar joints for the rail and interlocked incubator doors."""

from typing import Literal

from pydantic import Field, model_validator

from packages.info.program import ProgramConfig, Reject


class PassageJointProgram:
    id, type, tick_priority = "cell_passage.joint", "passage-joint/v1", 10

    class Config(ProgramConfig):
        controlEntity: str
        jointKey: str = Field(json_schema_extra={"spatialReference": "joint"})
        lower: float = Field(allow_inf_nan=False)
        upper: float = Field(allow_inf_nan=False)
        maxSpeed: float = Field(gt=0, allow_inf_nan=False)
        tolerance: float = Field(default=0.001, gt=0)
        velocityTolerance: float = Field(default=0.01, gt=0)
        role: Literal["axis", "outer", "inner"] = "axis"
        peerDoor: str | None = None

        @model_validator(mode="after")
        def limits_and_peer(self):
            if self.lower >= self.upper or self.role != "axis" and not self.peerDoor:
                raise ValueError("Ordered limits and door peers are required")
            return self

    def init(self, ctx):
        self.joint = ctx.physics.bind_joint(ctx.config.jointKey)
        self.handle, self.cancel_pending, self.failure = None, False, None
        self.target = self.commanded = float(ctx.physics.joint_q(self.joint)[0])
        self.stable = self.elapsed = 0.0
        self._publish(ctx)

    def _publish(self, ctx):
        q = float(ctx.physics.joint_q(self.joint)[0])
        qd = float(ctx.physics.joint_qd(self.joint)[0])
        ctx.set(
            position=q,
            velocity=qd,
            opened=abs(q - ctx.config.upper) < ctx.config.tolerance,
            closed=abs(q - ctx.config.lower) < ctx.config.tolerance,
            phase="idle" if self.handle is None else "stopping" if self.cancel_pending else "moving",
            controlSafe=self.handle is None and abs(qd) < ctx.config.velocityTolerance,
        )

    def _guard(self, inv, ctx):
        from packages.programs.cell_passage.guard import guard

        failed = guard(ctx, ctx.config.controlEntity, inv.params, [ctx.key])
        if failed:
            return failed
        role = ctx.config.role
        if (role, inv.action) in (("inner", "open"), ("outer", "close")):
            needed = "opened" if role == "inner" else "closed"
            if (
                ctx.read(ctx.config.peerDoor, needed) is not True
                or ctx.read(ctx.config.peerDoor, "phase") != "idle"
            ):
                return Reject(
                    code="door_interlock", message="The peer door is not in the required settled pose"
                )

    def accept(self, inv, ctx):
        failed = self._guard(inv, ctx)
        if failed:
            return failed
        if inv.action == "move" and not ctx.config.lower <= inv.params["position"] <= ctx.config.upper:
            return Reject(code="joint_limit", message="Requested target exceeds fixed scene limits")

    def _hold(self, ctx):
        self.target = self.commanded = float(ctx.physics.joint_q(self.joint)[0])
        ctx.physics.set_joint_target(self.joint, [self.target])
        ctx.physics.set_joint_target_velocity(self.joint, [0.0])

    def start(self, inv, ctx):
        failed = self.accept(inv, ctx)
        if failed:
            inv.fail(failed.code, failed.message)
            return
        if inv.action == "stop":
            if self.handle:
                self.handle.cancelled()
            self._hold(ctx)
        self.handle, self.cancel_pending, self.failure = inv, False, None
        self.elapsed = self.stable = 0.0
        if inv.action != "stop":
            self.commanded = float(ctx.physics.joint_q(self.joint)[0])
            self.target = (
                ctx.config.upper
                if inv.action == "open"
                else ctx.config.lower
                if inv.action == "close"
                else inv.params["position"]
            )
        self.deadline = abs(self.target - self.commanded) / ctx.config.maxSpeed * 2 + 3
        self._publish(ctx)

    def cancel(self, inv, ctx):
        if self.handle and inv.id == self.handle.id:
            self.cancel_pending = True
            self._hold(ctx)

    def tick(self, ctx, dt):
        inv = self.handle
        if inv is None:
            self._publish(ctx)
            return
        failed = self._guard(inv, ctx) if not self.cancel_pending else None
        if failed:
            self.failure = failed
            self.cancel_pending = True
            self._hold(ctx)
        self.elapsed += dt
        if self.elapsed > self.deadline and not self.cancel_pending:
            self.failure = Reject(code="motion_timeout", message="Actual scalar joint did not settle")
            self.cancel_pending = True
            self._hold(ctx)
        distance = ctx.config.maxSpeed * dt
        self.commanded += max(-distance, min(distance, self.target - self.commanded))
        ctx.physics.set_joint_target(self.joint, [self.commanded])
        ctx.physics.set_joint_target_velocity(self.joint, [0.0])
        q, qd = float(ctx.physics.joint_q(self.joint)[0]), float(ctx.physics.joint_qd(self.joint)[0])
        settled = abs(q - self.target) < ctx.config.tolerance and abs(qd) < ctx.config.velocityTolerance
        self.stable = self.stable + dt if settled else 0.0
        if self.stable >= 0.1:
            if self.failure:
                inv.fail(self.failure.code, self.failure.message)
            elif self.cancel_pending:
                inv.cancelled()
            else:
                inv.succeed()
            self.handle = None
        self._publish(ctx)
