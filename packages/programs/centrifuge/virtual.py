"""Simulation-time centrifuge with the same declared contract as the Newton program."""

from pydantic import Field

from packages.info.program import ProgramConfig, Reject


def balanced(slots):
    occupied = [i for i, value in enumerate(slots) if value is not None]
    return len(occupied) in (0, 4) or occupied in ([0, 2], [1, 3])


class CentrifugeProgram:
    id, type, tick_priority = "centrifuge.virtual", "centrifuge/v1", 30

    class Config(ProgramConfig):
        slotSites: list[str] = Field(
            min_length=4, max_length=4, json_schema_extra={"spatialReference": "site"}
        )
        tubes: list[str]
        visualRpmCap: float = Field(default=60, gt=0)

    def init(self, ctx):
        self.handle = None
        self.stopping = False
        self.elapsed = self.running = 0.0
        ctx.set(phase="idle", lidOpen=True, rpm=0, remainingSeconds=0, slots=[None] * 4)

    def accept(self, inv, ctx):
        if inv.action == "start" and not balanced(ctx.get("slots")):
            return Reject(
                code="imbalanced",
                message="Load zero, two opposite, or four tubes",
                details={"slots": ctx.get("slots")},
            )

    def start(self, inv, ctx):
        if inv.action == "stop":
            self.stopping = True
            inv.succeed()
            return
        self.handle, self.elapsed, self.running, self.stopping = inv, 0.0, 0.0, False
        if inv.action == "start":
            ctx.set(phase="accelerating", remainingSeconds=inv.params["durationSeconds"])

    def cancel(self, inv, ctx):
        if self.handle is None:
            self.handle = inv
        self.stopping = True

    def tick(self, ctx, dt):
        slots = [
            next((key for key in ctx.config.tubes if ctx.read(key, "location") == site), None)
            for site in ctx.config.slotSites
        ]
        ctx.set(slots=slots)
        inv = self.handle
        if inv is None:
            return
        self.elapsed += dt
        if inv.action in ("openLid", "closeLid"):
            if self.stopping:
                inv.cancelled()
                self.handle = None
            elif self.elapsed + 1e-9 >= 1:
                ctx.set(lidOpen=inv.action == "openLid")
                inv.succeed()
                self.handle = None
            return
        if inv.action != "start":
            inv.cancelled()
            self.handle = None
            return
        phase, rpm = ctx.get("phase"), ctx.get("rpm")
        target, duration = inv.params["rpm"], inv.params["durationSeconds"]
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
            if rpm < 1e-8:
                rpm, phase = 0.0, "idle"
        ctx.set(phase=phase, rpm=rpm, remainingSeconds=max(0, duration - self.running))
        inv.progress(min(0.999, self.elapsed / (2 * target / 1000 + duration)))
        if phase == "idle":
            ctx.set(remainingSeconds=0)
            if self.stopping:
                inv.cancelled()
            else:
                ctx.emit("runCompleted", rpm=target, durationSeconds=duration)
                inv.succeed()
            self.handle = None
