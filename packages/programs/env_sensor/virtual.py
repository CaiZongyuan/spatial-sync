"""Seeded ambient temperature and humidity readings."""

from pydantic import Field, FiniteFloat

from packages.info.program import ProgramConfig
from packages.programs.signals import lag, noisy


class EnvSensorProgram:
    id, type, tick_priority = "env_sensor.virtual", "env-sensor/v1", 100

    class Config(ProgramConfig):
        initialTemperatureC: FiniteFloat = Field(default=21, ge=-40, le=80)
        initialHumidityPercent: FiniteFloat = Field(default=40, ge=0, le=100)
        temperatureC: FiniteFloat = Field(default=22, ge=-40, le=80)
        humidityPercent: FiniteFloat = Field(default=45, ge=0, le=100)
        timeConstantSeconds: FiniteFloat = Field(default=60, gt=0)

    def init(self, ctx):
        self.temperature = ctx.config.initialTemperatureC
        self.humidity = ctx.config.initialHumidityPercent
        ctx.set(temperatureC=self.temperature, humidityPercent=self.humidity)

    def tick(self, ctx, dt):
        self.temperature = lag(self.temperature, ctx.config.temperatureC, dt, ctx.config.timeConstantSeconds)
        self.humidity = lag(self.humidity, ctx.config.humidityPercent, dt, ctx.config.timeConstantSeconds)
        if ctx.fact_due:
            ctx.set(
                temperatureC=noisy(ctx, self.temperature, 0.03, -40, 80),
                humidityPercent=noisy(ctx, self.humidity, 0.05, 0, 100),
            )
