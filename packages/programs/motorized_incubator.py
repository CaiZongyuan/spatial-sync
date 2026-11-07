"""Source motorized door ordering, separate from environmental telemetry."""

from pydantic import Field, StrictStr, model_validator

from packages.info.program import Reject

from .joint_drive import DriveConfig, JointDriveProgram, bind_axes, distinct_joints


class MotorizedIncubatorProgram(JointDriveProgram):
    id, type = "motorized_incubator.newton", "motorized-incubator/v1"

    class Config(DriveConfig):
        outerJoint: StrictStr = Field(json_schema_extra={"spatialReference": "joint"})
        innerJoint: StrictStr = Field(json_schema_extra={"spatialReference": "joint"})
        outerOpenAngle: float
        innerOpenAngle: float
        outerClosedAngle: float = 0.
        innerClosedAngle: float = 0.
        positionToleranceRad: float = Field(default=.001, gt=0, le=.0017453292519943296)
        velocityToleranceRadPerSecond: float = Field(default=.01, gt=0, le=.1)

        @model_validator(mode="after")
        def doors(self):
            distinct_joints([self.outerJoint, self.innerJoint])
            if any(abs(opened - closed) <= 2 * self.positionToleranceRad for opened, closed in (
                (self.outerOpenAngle, self.outerClosedAngle), (self.innerOpenAngle, self.innerClosedAngle)
            )):
                raise ValueError("Door open and closed poses must be distinct")
            return self

    @staticmethod
    def owned_joint_keys(config):
        return config.outerJoint, config.innerJoint

    def bind(self, ctx):
        return bind_axes(ctx.physics, self.owned_joint_keys(ctx.config), "revolute",
                         ctx.config.positionToleranceRad, ctx.config.velocityToleranceRadPerSecond)

    @staticmethod
    def configured_targets(config):
        return [{config.outerJoint: config.outerOpenAngle, config.innerJoint: config.innerOpenAngle},
                {config.outerJoint: config.outerClosedAngle, config.innerJoint: config.innerClosedAngle}]

    @staticmethod
    def targets(action, params, config):
        return {"openOuter": {config.outerJoint: config.outerOpenAngle},
                "openInner": {config.innerJoint: config.innerOpenAngle},
                "closeInner": {config.innerJoint: config.innerClosedAngle},
                "closeOuter": {config.outerJoint: config.outerClosedAngle}}[action]

    def at(self, ctx, key, goal):
        axis = self.motion.axes[key]
        return (abs(self.motion.position(key) - goal) <= axis.position_tolerance
                and abs(self.motion.velocity(key)) <= axis.velocity_tolerance)

    def interlock(self, ctx, action):
        c = ctx.config
        if action == "openInner" and not self.at(ctx, c.outerJoint, c.outerOpenAngle):
            return Reject(code="precondition_failed",
                          message="Outer door must be physically open and settled")
        if action == "closeOuter" and not self.at(ctx, c.innerJoint, c.innerClosedAngle):
            return Reject(code="precondition_failed",
                          message="Inner door must be physically closed and settled")

    def publish(self, ctx):
        if self.motion is None:
            ctx.set(phase="fault")
            return
        c = ctx.config
        ctx.set(phase=self.phase(), outerAngle=self.motion.position(c.outerJoint),
                innerAngle=self.motion.position(c.innerJoint),
                outerVelocity=self.motion.velocity(c.outerJoint),
                innerVelocity=self.motion.velocity(c.innerJoint),
                outerOpen=bool(self.at(ctx, c.outerJoint, c.outerOpenAngle)),
                outerClosed=bool(self.at(ctx, c.outerJoint, c.outerClosedAngle)),
                innerOpen=bool(self.at(ctx, c.innerJoint, c.innerOpenAngle)),
                innerClosed=bool(self.at(ctx, c.innerJoint, c.innerClosedAngle)))
