"""Feedback-only drawer drive; occupancy and observations are not fabricated."""

from pydantic import Field, StrictStr, model_validator

from .joint_drive import DriveConfig, JointDriveProgram, bind_axes, distinct_joints


class PlateImagerProgram(JointDriveProgram):
    id, type = "plate_imager.newton", "plate-imager/v1"

    class Config(DriveConfig):
        drawerJoint: StrictStr = Field(json_schema_extra={"spatialReference": "joint"})
        retractedPositionM: float = Field(default=0., ge=0, le=.19)
        extendedPositionM: float = Field(default=.19, gt=0, le=.19)
        positionToleranceM: float = Field(default=.001, gt=0, le=.003)
        velocityToleranceMPerSecond: float = Field(default=.001, gt=0, le=.01)

        @model_validator(mode="after")
        def travel(self):
            distinct_joints([self.drawerJoint])
            if self.extendedPositionM - self.retractedPositionM <= 2 * self.positionToleranceM:
                raise ValueError("Drawer endpoints must have distinct physical feedback windows")
            return self

    @staticmethod
    def owned_joint_keys(config):
        return (config.drawerJoint,)

    def bind(self, ctx):
        return bind_axes(ctx.physics, [ctx.config.drawerJoint], "prismatic", ctx.config.positionToleranceM,
                         ctx.config.velocityToleranceMPerSecond)

    @staticmethod
    def configured_targets(config):
        return [{config.drawerJoint: value} for value in
                (config.retractedPositionM, config.extendedPositionM)]

    @staticmethod
    def targets(action, params, config):
        value = config.extendedPositionM if action == "extend" else config.retractedPositionM
        return {config.drawerJoint: value}

    def publish(self, ctx):
        if self.motion is None:
            ctx.set(phase="fault")
            return
        c, axis = ctx.config, self.motion.axes[ctx.config.drawerJoint]
        position, velocity = self.motion.position(c.drawerJoint), self.motion.velocity(c.drawerJoint)
        settled = abs(velocity) <= axis.velocity_tolerance
        ctx.set(phase=self.phase(), extensionM=position, velocityMPerSecond=velocity,
                extended=bool(settled and abs(position - c.extendedPositionM) <= axis.position_tolerance),
                retracted=bool(settled and abs(position - c.retractedPositionM) <= axis.position_tolerance))
