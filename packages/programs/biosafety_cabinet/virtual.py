"""Seeded airflow readings and immediate cabinet controls."""

from pydantic import Field, FiniteFloat

from packages.info.program import ProgramConfig
from packages.programs.signals import lag, noisy


class BiosafetyCabinetProgram:
    id, type, tick_priority = "biosafety_cabinet.virtual", "biosafety-cabinet/v1", 100

    class Config(ProgramConfig):
        initialInflowMs: FiniteFloat = Field(default=0.2, ge=0, le=2)
        sashHeightCm: FiniteFloat = Field(default=20, ge=0, le=60)
        blowerOn: bool = True
        uvOn: bool = False
        timeConstantSeconds: FiniteFloat = Field(default=3, gt=0)

    def init(self, ctx):
        self.inflow = ctx.config.initialInflowMs
        ctx.set(
            inflowMs=self.inflow,
            sashHeightCm=ctx.config.sashHeightCm,
            blowerOn=ctx.config.blowerOn,
            uvOn=ctx.config.uvOn,
        )

    def start(self, inv, ctx):
        ctx.set(**{"blowerOn" if inv.action == "setBlower" else "uvOn": inv.params["on"]})
        inv.succeed()

    def tick(self, ctx, dt):
        self.inflow = lag(self.inflow, 0.45 if ctx.get("blowerOn") else 0, dt, ctx.config.timeConstantSeconds)
        if ctx.fact_due:
            ctx.set(
                inflowMs=noisy(ctx, self.inflow, 0.005, 0, 2),
                sashHeightCm=ctx.get("sashHeightCm"),
                uvOn=ctx.get("uvOn"),
                blowerOn=ctx.get("blowerOn"),
            )
