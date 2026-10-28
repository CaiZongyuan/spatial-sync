"""Passage-specific model mass interlock and physical rotor-cycle receipts."""

import math

import numpy as np
from pydantic import Field, model_validator

from packages.info.program import Reject
from packages.programs.centrifuge.loading import transformed_bounds
from packages.programs.centrifuge.newton import CentrifugeProgram


def modeled_slot_masses(slots, vessels, tare, density):
    result = []
    for key in slots:
        if key is None:
            result.append(0.0)
            continue
        contents = [row for row in vessels if row["entityKey"] == key]
        if len(contents) != 1 or key not in tare:
            raise ValueError("Loaded tube has unknown or ambiguous modeled contents/tare")
        volume = contents[0]["volumeUl"]
        mass = tare[key] + volume * density / 1_000_000
        if not math.isfinite(mass) or volume < 0 or tare[key] <= 0 or density <= 0:
            raise ValueError("Invalid modeled mass")
        result.append(mass)
    return result


class PassageCentrifugeProgram(CentrifugeProgram):
    id, type = "cell_passage.centrifuge", "passage-centrifuge/v1"

    class Config(CentrifugeProgram.Config):
        controlEntity: str
        armBodies: list[str] = Field(
            min_length=11, max_length=11, json_schema_extra={"spatialReference": "body"}
        )
        housingBody: str = Field(json_schema_extra={"spatialReference": "body"})
        tareKg: dict[str, float]
        densityKgPerL: float = Field(default=1, gt=0, allow_inf_nan=False)
        balanceToleranceKg: float = Field(default=0.00002, gt=0, allow_inf_nan=False)

        @model_validator(mode="after")
        def known_tare(self):
            if set(self.tareKg) != set(self.tubes) or any(
                not math.isfinite(v) or v <= 0 for v in self.tareKg.values()
            ):
                raise ValueError("Every tube needs a finite positive tare mass")
            return self

    def init(self, ctx):
        super().init(ctx)
        self.cycle_turns = 0.0
        self.previous_angle = float(ctx.physics.joint_q(self.spindle)[0])
        self.cycle_load = []
        self.arm_bodies = [ctx.physics.bind_body(key) for key in ctx.config.armBodies]
        self.housing_body = ctx.physics.bind_body(ctx.config.housingBody)
        self.arm_bounds = np.array([ctx.physics.collision_bounds(body) for body in self.arm_bodies])
        ctx.set(
            completedCycle="",
            completedLoad=[],
            cycleRunSeconds=0,
            actualRotorTurns=0,
            actualRpm=0,
            loadedEntities=self._slots(ctx),
            modeledMassKg=[0.0] * 4,
            controlSafe=True,
        )

    def _guard(self, inv, ctx):
        from packages.programs.cell_passage.guard import guard

        resources = [ctx.key]
        if inv.action == "start":
            resources.extend(
                key for key in (self.cycle_load if self.handle else self._loading(ctx)[0]) if key
            )
        failed = guard(ctx, ctx.config.controlEntity, inv.params, resources)
        if failed:
            return failed
        if inv.action in ("closeLid", "start"):
            try:
                actual = transformed_bounds(
                    self.arm_bounds, ctx.physics.body[self.arm_bodies], ctx.physics.body[self.housing_body]
                )
                if not np.isfinite(actual).all():
                    raise ValueError("Unknown physical arm pose")
                # Fixed swept access envelope for this hinged 400 mm lid. It is
                # intentionally conservative and depends on actual link poses,
                # never the robot's intent-derived zone label.
                overlaps = np.all(actual[:, 1] >= [-0.22, -0.22, -0.03], axis=1) & np.all(
                    actual[:, 0] <= [0.22, 0.22, 0.56], axis=1
                )
                if np.any(overlaps):
                    return Reject(
                        code="arm_in_zone", message="Retract all physical robot links from the lid envelope"
                    )
            except (ValueError, KeyError, TypeError):
                return Reject(code="unknown_clearance", message="Actual robot clearance is unconfirmed")

    def accept(self, inv, ctx):
        failed = self._guard(inv, ctx) or super().accept(inv, ctx)
        if failed or inv.action != "start":
            return failed
        slots, _ = self._loading(ctx)
        if not any(slots):
            return Reject(code="empty_load", message="A processing cycle requires actual loaded tubes")
        if any(ctx.read(key, "lidClosed") is not True for key in slots if key):
            return Reject(code="tube_open", message="Close every loaded tube cap before centrifugation")
        try:
            snapshot = ctx.read(ctx.config.controlEntity, "materialSnapshot")
            masses = modeled_slot_masses(
                slots, snapshot["vessels"], ctx.config.tareKg, ctx.config.densityKgPerL
            )
        except (ValueError, KeyError, TypeError):
            return Reject(code="unknown_mass", message="Current modeled load mass cannot be confirmed")
        if any(abs(masses[a] - masses[b]) > ctx.config.balanceToleranceKg for a, b in ((0, 2), (1, 3))):
            return Reject(
                code="mass_imbalanced",
                message="Opposite modeled masses exceed the fixed tolerance",
                details={"modeledMassKg": masses, "toleranceKg": ctx.config.balanceToleranceKg},
            )

    def start(self, inv, ctx):
        failed = self.accept(inv, ctx)
        if failed:
            inv.fail(failed.code, failed.message, **failed.details)
            return
        if inv.action == "start":
            self.cycle_load = self._loading(ctx)[0]
            self.cycle_turns = 0.0
            self.previous_angle = float(ctx.physics.joint_q(self.spindle)[0])
            snapshot = ctx.read(ctx.config.controlEntity, "materialSnapshot")
            masses = modeled_slot_masses(
                self.cycle_load, snapshot["vessels"], ctx.config.tareKg, ctx.config.densityKgPerL
            )
            ctx.set(
                completedCycle="",
                completedLoad=[],
                cycleRunSeconds=0,
                actualRotorTurns=0,
                modeledMassKg=masses,
            )
        ctx.set(controlSafe=False)
        super().start(inv, ctx)

    def tick(self, ctx, dt):
        inv = self.handle
        if inv is not None and self._guard(inv, ctx):
            self.stopping = True
            # Replace the old lid drive immediately; the rotor follows the
            # existing physical deceleration/alignment path while retaining load.
            if inv.action in ("openLid", "closeLid"):
                ctx.physics.set_joint_target(self.lid, ctx.physics.joint_q(self.lid))
        angle = float(ctx.physics.joint_q(self.spindle)[0])
        if inv is not None and inv.action == "start":
            self.cycle_turns += abs(angle - self.previous_angle) / (2 * math.pi)
        self.previous_angle = angle
        super().tick(ctx, dt)
        if inv is not None and inv.action == "start":
            ctx.set(cycleRunSeconds=self.running, actualRotorTurns=self.cycle_turns)
            if self.handle is None and inv.invocation.status == "succeeded":
                ctx.set(completedCycle=inv.params["operationId"], completedLoad=self.cycle_load)
        safe = (
            self.handle is None
            and abs(ctx.physics.joint_qd(self.spindle)[0]) < 0.05
            and abs(ctx.physics.joint_qd(self.lid)[0]) < 0.02
        )
        ctx.set(
            controlSafe=bool(safe),
            loadedEntities=self._slots(ctx),
            actualRpm=abs(float(ctx.physics.joint_qd(self.spindle)[0])) * 60 / (2 * math.pi),
        )
