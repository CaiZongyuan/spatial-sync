"""Importable by both the live browser host and its structural validation worker."""

from packages.programs.env_sensor.virtual import EnvSensorProgram


class QuietHumiditySensor(EnvSensorProgram):
    def tick(self, ctx, dt):
        # Initial humidity is real; no later write lets the host's actual stale
        # deadline expire. Temperature continues through the same Program API.
        if ctx.fact_due:
            ctx.set(temperatureC=ctx.config.temperatureC)
