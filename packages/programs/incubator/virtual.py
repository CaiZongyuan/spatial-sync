"""A seeded incubator signal model; all time is simulation time."""

from pydantic import Field, FiniteFloat

from packages.info.program import ProgramConfig
from packages.programs.signals import lag, noisy


class IncubatorProgram:
    id, type, tick_priority = "incubator.virtual", "incubator/v1", 100

    class Config(ProgramConfig):
        initialTemperatureC: FiniteFloat = Field(default=22, ge=0, le=80)
        initialCo2Percent: FiniteFloat = Field(default=0.04, ge=0, le=20)
        initialHumidityPercent: FiniteFloat = Field(default=45, ge=0, le=100)
        setpointC: FiniteFloat = Field(default=37, ge=20, le=60)
        doorOpen: bool = False
        timeConstantSeconds: FiniteFloat = Field(default=60, gt=0)
        doorTimeConstantSeconds: FiniteFloat = Field(default=20, gt=0)

    def init(self, ctx):
        self.temperature = ctx.config.initialTemperatureC
        self.co2 = ctx.config.initialCo2Percent
        self.humidity = ctx.config.initialHumidityPercent
        ctx.set(
            temperatureC=self.temperature,
            co2Percent=self.co2,
            humidityPercent=self.humidity,
            doorOpen=ctx.config.doorOpen,
            setpointC=ctx.config.setpointC,
        )

    def start(self, inv, ctx):
        ctx.set(setpointC=inv.params["temperatureC"])
        inv.succeed()

    def tick(self, ctx, dt):
        opened = ctx.get("doorOpen")
        tau = ctx.config.doorTimeConstantSeconds if opened else ctx.config.timeConstantSeconds
        self.temperature = lag(self.temperature, 22 if opened else ctx.get("setpointC"), dt, tau)
        self.co2 = lag(self.co2, 0.04 if opened else 5, dt, tau)
        self.humidity = lag(self.humidity, 45 if opened else 60, dt, tau)
        if ctx.fact_due:
            ctx.set(
                temperatureC=noisy(ctx, self.temperature, 0.03, 0, 80),
                co2Percent=noisy(ctx, self.co2, 0.005, 0, 20),
                humidityPercent=noisy(ctx, self.humidity, 0.05, 0, 100),
                doorOpen=opened,
                setpointC=ctx.get("setpointC"),
            )
