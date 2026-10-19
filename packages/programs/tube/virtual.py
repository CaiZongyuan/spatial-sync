"""A tube writes its own location by observing the arm's declared state."""

from pydantic import Field

from packages.info.program import ProgramConfig


class TubeProgram:
    id, type, tick_priority = "tube.virtual", "tube/v1", 20

    class Config(ProgramConfig):
        robots: list[str]
        initialLocations: dict[str, str]
        locations: dict = Field(json_schema_extra={"documentSchema": "locations"})

    def init(self, ctx):
        self.holder = None
        ctx.set(location=ctx.config.initialLocations.get(ctx.key, "loose"), upright=True)

    def tick(self, ctx, dt):
        holder = next((robot for robot in ctx.config.robots if ctx.read(robot, "holding") == ctx.key), None)
        if holder:
            ctx.set(location="held", upright=True)
        elif self.holder:
            name = ctx.read(self.holder, "atLocation")
            location = ctx.config.locations["locations"].get(name)
            if location:
                ctx.set(location=location["site"], upright=True)
            else:
                ctx.set(location="loose")
                ctx.emit("dropped", lastHolder=self.holder)
        self.holder = holder
