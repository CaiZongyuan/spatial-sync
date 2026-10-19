"""Tube properties are derived from staged physical poses, never action intent."""

from pydantic import Field

from packages.info.program import ProgramConfig

from .facts import tube_location, upright


class TubeProgram:
    id, type, tick_priority = "tube.newton", "tube/v1", 20

    class Config(ProgramConfig):
        robots: list[str]
        locationSites: list[str] = Field(json_schema_extra={"spatialReference": "site"})

    def init(self, ctx):
        self.body = ctx.physics.bind_body(ctx.key)
        self.sites = ctx.physics.bind_sites(ctx.config.locationSites)
        self._facts(ctx, initial=True)

    def _facts(self, ctx, initial=False):
        port = ctx.physics
        holder = next((robot for robot in ctx.config.robots if ctx.read(robot, "holding") == ctx.key), None)
        poses = port.site_poses(self.sites)
        velocity = port.relative_site_velocity(self.body, self.sites)
        location = tube_location(
            port.body[self.body],
            velocity,
            poses,
            ctx.config.locationSites,
            held=holder is not None,
        )
        if (
            not initial
            and ctx.get("location") == "held"
            and location == "loose"
            and ctx.key not in port.releasing
        ):
            ctx.emit("dropped", lastHolder=self.holder)
        ctx.set(location=location, upright=bool(upright(port.body[self.body, 3:])))
        self.holder = holder

    def tick(self, ctx, dt):
        if ctx.fact_due:
            self._facts(ctx)
