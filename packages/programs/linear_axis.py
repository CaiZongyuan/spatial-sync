"""A separately owned meter-valued rail; no implicit arm motion."""

from pydantic import Field, StrictStr, model_validator

from .joint_drive import DriveConfig, JointDriveProgram, bind_axes, distinct_joints


class LinearAxisProgram(JointDriveProgram):
    id, type = "linear_axis.newton", "linear-axis/v1"

    class Config(DriveConfig):
        jointKey: StrictStr = Field(json_schema_extra={"spatialReference": "joint"})
        minPositionM: float = Field(default=0, ge=0, le=2.95)
        maxPositionM: float = Field(default=2.95, gt=0, le=2.95)
        homePositionM: float = 0.
        positionToleranceM: float = Field(default=.001, gt=0, le=.003)
        velocityToleranceMPerSecond: float = Field(default=.001, gt=0, le=.01)

        @model_validator(mode="after")
        def limits(self):
            distinct_joints([self.jointKey])
            if not self.minPositionM <= self.homePositionM <= self.maxPositionM:
                raise ValueError("Rail home must lie within its configured travel")
            return self

    @staticmethod
    def owned_joint_keys(config):
        return (config.jointKey,)

    def bind(self, ctx):
        return bind_axes(ctx.physics, [ctx.config.jointKey], "prismatic", ctx.config.positionToleranceM,
                         ctx.config.velocityToleranceMPerSecond)

    @staticmethod
    def configured_targets(config):
        return [{config.jointKey: value} for value in
                (config.minPositionM, config.maxPositionM, config.homePositionM)]

    @staticmethod
    def targets(action, params, config):
        return {config.jointKey: config.homePositionM if action == "home" else params["positionM"]}

    def accept(self, inv, ctx):
        if inv.action == "moveTo" and not ctx.config.minPositionM <= inv.params["positionM"] <= (
            ctx.config.maxPositionM
        ):
            from packages.info.program import Reject

            return Reject(code="invalid_params", message="Target exceeds configured rail travel")
        return super().accept(inv, ctx)

    def publish(self, ctx):
        if self.motion is None:
            ctx.set(phase="fault")
            return
        ctx.set(phase=self.phase(), positionM=self.motion.position(ctx.config.jointKey),
                velocityMPerSecond=self.motion.velocity(ctx.config.jointKey))
