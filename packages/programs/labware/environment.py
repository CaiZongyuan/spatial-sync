"""Explicit signal models for the physical passage enclosure and incubator.

Only the door observations come from Newton joints. Temperature, gases,
humidity, sash and airflow remain the existing seeded teaching signal models.
"""

import math

from pydantic import Field, FiniteFloat, model_validator

from packages.info.program import ProgramConfig
from packages.programs.biosafety_cabinet.virtual import BiosafetyCabinetProgram
from packages.programs.cell_passage.guard import guard
from packages.programs.incubator.virtual import IncubatorProgram


class PassageIncubatorProgram(IncubatorProgram):
    id, type = "cell_passage.incubator", "passage-incubator/v1"

    class Config(ProgramConfig):
        controlEntity: str
        outerDoorJoint: str = Field(json_schema_extra={"spatialReference": "joint"})
        innerDoorJoint: str = Field(json_schema_extra={"spatialReference": "joint"})
        initialTemperatureC: FiniteFloat = Field(default=37, ge=0, le=80)
        initialCo2Percent: FiniteFloat = Field(default=5, ge=0, le=20)
        initialHumidityPercent: FiniteFloat = Field(default=95, ge=0, le=100)
        setpointC: FiniteFloat = Field(default=37, ge=20, le=60)
        timeConstantSeconds: FiniteFloat = Field(default=60, gt=0)
        doorTimeConstantSeconds: FiniteFloat = Field(default=20, gt=0)
        targetCo2Percent: FiniteFloat = Field(default=5, ge=0, le=20)
        targetHumidityPercent: FiniteFloat = Field(default=95, ge=0, le=100)
        temperatureRangeC: list[FiniteFloat] = Field(
            default_factory=lambda: [36.5, 37.5], min_length=2, max_length=2
        )
        co2RangePercent: list[FiniteFloat] = Field(
            default_factory=lambda: [4.5, 5.5], min_length=2, max_length=2
        )
        humidityRangePercent: list[FiniteFloat] = Field(
            default_factory=lambda: [90, 100], min_length=2, max_length=2
        )

        @model_validator(mode="after")
        def ready_ranges(self):
            for bounds, target, maximum in (
                (self.temperatureRangeC, self.setpointC, 80),
                (self.co2RangePercent, self.targetCo2Percent, 20),
                (self.humidityRangePercent, self.targetHumidityPercent, 100),
            ):
                if not 0 <= bounds[0] <= target <= bounds[1] <= maximum:
                    raise ValueError("Teaching readiness ranges must enclose their configured targets")
            return self

    def _targets(self, ctx):
        return ctx.get("setpointC"), ctx.config.targetCo2Percent, ctx.config.targetHumidityPercent

    def init(self, ctx):
        self.doors = [
            ctx.physics.bind_joint(key) for key in (ctx.config.outerDoorJoint, ctx.config.innerDoorJoint)
        ]
        self.temperature, self.co2, self.humidity = (
            ctx.config.initialTemperatureC,
            ctx.config.initialCo2Percent,
            ctx.config.initialHumidityPercent,
        )
        self.handle, self.waited, self.timeout = None, 0.0, 90.0
        opened, known = self._doors(ctx)
        ctx.set(
            temperatureC=self.temperature,
            co2Percent=self.co2,
            humidityPercent=self.humidity,
            doorOpen=opened,
            doorKnown=known,
            setpointC=ctx.config.setpointC,
            signalSource="modeled",
            controlSafe=True,
            phase="idle",
            waitElapsedSeconds=0.0,
            co2SetpointPercent=ctx.config.targetCo2Percent,
            humiditySetpointPercent=ctx.config.targetHumidityPercent,
            temperatureReadyRangeC=ctx.config.temperatureRangeC,
            co2ReadyRangePercent=ctx.config.co2RangePercent,
            humidityReadyRangePercent=ctx.config.humidityRangePercent,
        )
        self._ready(ctx, opened, known)

    def _doors(self, ctx):
        try:
            readings = [
                (float(ctx.physics.joint_q(joint)[0]), float(ctx.physics.joint_qd(joint)[0]))
                for joint in self.doors
            ]
            known = all(math.isfinite(value) for reading in readings for value in reading)
            closed = known and all(abs(q) < 0.005 and abs(qd) < 0.02 for q, qd in readings)
            return not closed, known
        except (ValueError, KeyError, TypeError):
            return True, False

    def _ready(self, ctx, opened, known):
        try:
            ready = (
                known
                and not opened
                and abs(ctx.get("setpointC") - ctx.config.setpointC) <= 0.1
                and ctx.config.temperatureRangeC[0]
                <= ctx.get("temperatureC")
                <= ctx.config.temperatureRangeC[1]
                and ctx.config.co2RangePercent[0] <= ctx.get("co2Percent") <= ctx.config.co2RangePercent[1]
                and ctx.config.humidityRangePercent[0]
                <= ctx.get("humidityPercent")
                <= ctx.config.humidityRangePercent[1]
            )
        except (TypeError, KeyError):
            ready = False
        ctx.set(environmentReady=bool(ready))

    def accept(self, inv, ctx):
        return guard(ctx, ctx.config.controlEntity, inv.params, [ctx.key])

    def start(self, inv, ctx):
        failed = self.accept(inv, ctx)
        if failed:
            inv.fail(failed.code, failed.message)
            return
        if inv.action == "waitReady":
            self.waited, self.timeout = 0.0, inv.params.get("timeoutSeconds", 90.0)
            ctx.set(waitElapsedSeconds=0.0)
            if ctx.get("environmentReady") is True:
                inv.succeed()
            else:
                self.handle = inv
                ctx.set(phase="waiting")
            return
        ctx.set(setpointC=inv.params["temperatureC"])
        self._ready(ctx, *self._doors(ctx))
        inv.succeed()

    def cancel(self, inv, ctx):
        if self.handle and self.handle.id == inv.id:
            self.handle = None
            ctx.set(phase="idle")
            inv.cancelled()

    def tick(self, ctx, dt):
        opened, known = self._doors(ctx)
        ctx.set(doorOpen=opened, doorKnown=known)
        super().tick(ctx, dt)
        self._ready(ctx, opened, known)
        if self.handle:
            inv = self.handle
            self.waited += dt
            failed = self.accept(inv, ctx)
            if failed:
                inv.fail(failed.code, failed.message)
                self.handle = None
            elif ctx.get("environmentReady") is True:
                inv.succeed()
                self.handle = None
            elif self.waited >= self.timeout:
                inv.fail("readiness_timeout", "Modeled conditions or actual doors did not become ready")
                self.handle = None
            ctx.set(waitElapsedSeconds=self.waited, phase="waiting" if self.handle else "idle")


class PassageCabinetProgram(BiosafetyCabinetProgram):
    id, type = "cell_passage.cabinet", "passage-cabinet/v1"

    class Config(BiosafetyCabinetProgram.Config):
        controlEntity: str
        initialInflowMs: FiniteFloat = Field(default=0.45, ge=0, le=2)

    def init(self, ctx):
        super().init(ctx)
        ctx.set(signalSource="modeled", controlSafe=True)
        ctx.set(cabinetReady=self._ready(ctx))

    def _ready(self, ctx, **overrides):
        def value(key):
            return overrides[key] if key in overrides else ctx.get(key)

        try:
            return bool(
                value("blowerOn") is True
                and value("uvOn") is False
                and 0.4 <= value("inflowMs") <= 2
                and 18 <= value("sashHeightCm") <= 22
            )
        except (TypeError, KeyError):
            return False

    def accept(self, inv, ctx):
        return guard(ctx, ctx.config.controlEntity, inv.params, [ctx.key])

    def start(self, inv, ctx):
        failed = self.accept(inv, ctx)
        if failed:
            inv.fail(failed.code, failed.message)
            return
        prop = "blowerOn" if inv.action == "setBlower" else "uvOn"
        update = {prop: inv.params["on"]}
        ctx.set(**update, cabinetReady=self._ready(ctx, **update))
        inv.succeed()

    def tick(self, ctx, dt):
        super().tick(ctx, dt)
        ctx.set(cabinetReady=self._ready(ctx))
