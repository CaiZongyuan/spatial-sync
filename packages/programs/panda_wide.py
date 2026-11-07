"""Seven angular drives and the source Panda's two offset finger slides."""

from pydantic import Field, FiniteFloat, StrictStr, model_validator

from .joint_drive import DriveConfig, JointDriveProgram, bind_axes, distinct_joints

OPENING_OFFSET = .11


class PandaWideProgram(JointDriveProgram):
    id, type, tick_priority = "panda_wide.newton", "panda-wide-arm/v1", 10

    class Config(DriveConfig):
        jointKeys: list[StrictStr] = Field(min_length=7, max_length=7,
                                           json_schema_extra={"spatialReference": "joint"})
        fingerKeys: list[StrictStr] = Field(min_length=2, max_length=2,
                                            json_schema_extra={"spatialReference": "joint"})
        homePositions: list[FiniteFloat] = Field(min_length=7, max_length=7)
        positionToleranceRad: float = Field(default=.001, gt=0, le=.01)
        velocityToleranceRadPerSecond: float = Field(default=.01, gt=0, le=.1)
        fingerToleranceM: float = Field(default=.00025, gt=0, le=.001)
        fingerVelocityToleranceMPerSecond: float = Field(default=.001, gt=0, le=.01)

        @model_validator(mode="after")
        def unique_drives(self):
            distinct_joints([*self.jointKeys, *self.fingerKeys])
            return self

    @staticmethod
    def owned_joint_keys(config):
        return tuple([*config.jointKeys, *config.fingerKeys])

    def bind(self, ctx):
        c = ctx.config
        return bind_axes(ctx.physics, c.jointKeys, "revolute", c.positionToleranceRad,
                         c.velocityToleranceRadPerSecond) | bind_axes(
            ctx.physics, c.fingerKeys, "prismatic", c.fingerToleranceM, c.fingerVelocityToleranceMPerSecond)

    @staticmethod
    def configured_targets(config):
        return [dict(zip(config.jointKeys, config.homePositions, strict=True)),
                dict.fromkeys(config.fingerKeys, 0.), dict.fromkeys(config.fingerKeys, .04)]

    @staticmethod
    def targets(action, params, config):
        if action == "setGripper":
            value = .04 if params["openingM"] == .19 else (params["openingM"] - OPENING_OFFSET) / 2
            return dict.fromkeys(config.fingerKeys, value)
        positions = config.homePositions if action == "home" else params["positions"]
        return dict(zip(config.jointKeys, positions, strict=True))

    def publish(self, ctx):
        if self.motion is None:
            ctx.set(phase="fault")
            return
        ctx.set(phase=self.phase(),
                jointPositions=[self.motion.position(key) for key in ctx.config.jointKeys],
                jointVelocities=[self.motion.velocity(key) for key in ctx.config.jointKeys],
                openingM=OPENING_OFFSET + sum(self.motion.position(key) for key in ctx.config.fingerKeys))
