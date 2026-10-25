"""Physical lid and velocity-driven rotor, with simulation-time run phases."""

import math

from pydantic import Field

from packages.info.program import ProgramConfig, Reject

from .virtual import balanced


class CentrifugeProgram:
    id, type, tick_priority = "centrifuge.newton", "centrifuge/v1", 30

    class Config(ProgramConfig):
        slotSites: list[str] = Field(
            min_length=4, max_length=4, json_schema_extra={"spatialReference": "site"}
        )
        tubes: list[str] = Field(json_schema_extra={"spatialReference": "body"})
        spindleJoint: str = Field(json_schema_extra={"spatialReference": "joint"})
        lidJoint: str = Field(json_schema_extra={"spatialReference": "joint"})
        visualRpmCap: float = Field(default=60, gt=0)

    def init(self, ctx):
        port = ctx.physics
        self.spindle, self.lid = (
            port.bind_joint(ctx.config.spindleJoint),
            port.bind_joint(ctx.config.lidJoint),
        )
        self.slot_bodies = [port.bind_site(key).body for key in ctx.config.slotSites]
        self.tubes = {key: port.bind_body(key) for key in ctx.config.tubes}
        self.handle, self.stopping, self.stop_angle = None, False, None
        self.elapsed, self.running = 0.0, 0.0
        self.rotor_velocity = 0.0
        self.welded = []
        ctx.set(
            phase="idle",
            lidOpen=bool(port.joint_q(self.lid)[0] > 0.1),
            rpm=0,
            remainingSeconds=0,
            slots=self._slots(ctx),
        )

    def _slots(self, ctx):
        return [
            next((key for key in self.tubes if ctx.read(key, "location") == site), None)
            for site in ctx.config.slotSites
        ]

    def accept(self, inv, ctx):
        if inv.action == "start" and not balanced(self._slots(ctx)):
            return Reject(
                code="imbalanced",
                message="Load zero, two opposite, or four tubes",
                details={"slots": self._slots(ctx)},
            )

    def start(self, inv, ctx):
        if inv.action == "stop":
            self.stopping = True
            inv.succeed()
            return
        self.handle, self.stopping, self.stop_angle = inv, False, None
        self.elapsed, self.running = 0.0, 0.0
        port = ctx.physics
        if inv.action in ("openLid", "closeLid"):
            port.set_joint_target(self.lid, [1.4 if inv.action == "openLid" else 0])
        else:
            for slot, tube in zip(self.slot_bodies, self._slots(ctx), strict=True):
                if tube:
                    port.weld(slot, self.tubes[tube])
                    self.welded.append((slot, self.tubes[tube]))
            ctx.set(phase="accelerating", remainingSeconds=inv.params["durationSeconds"])

    def cancel(self, inv, ctx):
        self.handle = self.handle or inv
        self.stopping = True

    def tick(self, ctx, dt):
        port = ctx.physics
        ctx.set(lidOpen=bool(port.joint_q(self.lid)[0] > 0.1))
        if ctx.fact_due:
            ctx.set(slots=self._slots(ctx))
        inv = self.handle
        if inv is None:
            return
        self.elapsed += dt
        if inv.action in ("openLid", "closeLid"):
            if self.stopping:
                port.set_joint_target(self.lid, port.joint_q(self.lid))
                inv.cancelled()
                self.handle = None
            elif abs(port.joint_q(self.lid)[0] - (1.4 if inv.action == "openLid" else 0)) < 0.02:
                inv.succeed()
                self.handle = None
            return
        target, duration = inv.params["rpm"], inv.params["durationSeconds"]
        rpm, phase = ctx.get("rpm"), ctx.get("phase")
        if self.stopping:
            phase = "decelerating"
        if phase == "accelerating":
            rpm = min(target, rpm + 1000 * dt)
            if rpm >= target:
                phase = "running"
        elif phase == "running":
            self.running = min(duration, self.running + dt)
            if self.running + 1e-9 >= duration:
                phase = "decelerating"
        elif phase == "decelerating":
            rpm = max(0.0, rpm - 1000 * dt)
        velocity = min(rpm, ctx.config.visualRpmCap) / 60 * 2 * math.pi
        if phase == "decelerating" and rpm == 0:
            angle = float(port.joint_q(self.spindle)[0])
            if self.stop_angle is None:
                # Leave room for the physical drive to settle before alignment.
                speed = max(self.rotor_velocity, abs(float(port.joint_qd(self.spindle)[0])))
                self.stop_angle = math.ceil((angle + speed * 0.5 + 0.005) / (2 * math.pi)) * 2 * math.pi
            error = self.stop_angle - angle
            velocity = max(0.0, min(ctx.config.visualRpmCap / 60 * 2 * math.pi, error))
            if abs(error) < 0.005 and abs(port.joint_qd(self.spindle)[0]) < 0.05:
                velocity, phase = 0.0, "idle"
        # The displayed ramp can reach the visual cap in one control sample.
        # Give newly enabled slot welds a gentle startup. The faster braking
        # response and unit alignment gain settle without reversing the rotor.
        response = 1.0 if velocity > self.rotor_velocity else 0.25
        self.rotor_velocity += -math.expm1(-dt / response) * (velocity - self.rotor_velocity)
        if phase == "idle":
            self.rotor_velocity = 0.0
        port.set_joint_target_velocity(self.spindle, [self.rotor_velocity])
        ctx.set(phase=phase, rpm=rpm, remainingSeconds=max(0, duration - self.running))
        inv.progress(min(0.999, self.elapsed / (2 * target / 1000 + duration + 1)))
        if phase == "idle":
            for slot, tube in self.welded:
                port.unweld(slot, tube)
            self.welded.clear()
            ctx.set(remainingSeconds=0)
            if self.stopping:
                inv.cancelled()
            else:
                ctx.emit("runCompleted", rpm=target, durationSeconds=duration)
                inv.succeed()
            self.handle = None
