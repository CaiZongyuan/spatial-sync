"""Owned scalar joints for the rail and interlocked incubator doors."""

from typing import Literal

import numpy as np
from pydantic import Field, FiniteFloat, model_validator

from packages.info.program import ProgramConfig, Reject
from packages.programs.centrifuge.loading import rotation, transformed_bounds

from .contacts import (
    CONTACT_LIMIT_M,
    contacts_for_bodies,
    validate_expected_contacts,
    validate_seating_contacts,
)

Vector = tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class PassageJointProgram:
    id, type, tick_priority = "cell_passage.joint", "passage-joint/v1", 10

    class Config(ProgramConfig):
        controlEntity: str
        jointKey: str = Field(json_schema_extra={"spatialReference": "joint"})
        lower: float = Field(allow_inf_nan=False)
        upper: float = Field(allow_inf_nan=False)
        maxSpeed: float = Field(gt=0, allow_inf_nan=False)
        tolerance: float = Field(default=0.001, gt=0, allow_inf_nan=False)
        velocityTolerance: float = Field(default=0.01, gt=0, allow_inf_nan=False)
        contactAllowanceM: float = Field(default=0.001, gt=0, le=0.002, allow_inf_nan=False)
        seatingContactPairs: list[tuple[str, str]] = Field(default_factory=list, max_length=8)
        seatingContactAllowanceM: float = Field(default=0.002, gt=0, le=0.005, allow_inf_nan=False)
        qualifiedPlacement: bool = False
        expectedContactPairs: list[tuple[str, str]] = Field(default_factory=list)
        fingerBodyKeys: list[str] = Field(
            default_factory=list, json_schema_extra={"spatialReference": "body"}
        )
        role: Literal["axis", "outer", "inner"] = "axis"
        peerDoor: str | None = None
        movingBodyKeys: list[str] = Field(
            default_factory=list, json_schema_extra={"spatialReference": "body"}
        )
        clearanceBodyKeys: list[str] = Field(
            default_factory=list, json_schema_extra={"spatialReference": "body"}
        )
        armEntity: str | None = Field(
            default=None,
            exclude_if=lambda value: value is None,
            json_schema_extra={"spatialReference": "body"},
        )
        attachedLids: dict[str, str] = Field(default_factory=dict)
        sweepAnchorBody: str | None = Field(
            default=None,
            exclude_if=lambda value: value is None,
            json_schema_extra={"spatialReference": "body"},
        )
        sweepLowerLocalM: Vector | None = None
        sweepUpperLocalM: Vector | None = None

        @model_validator(mode="after")
        def limits_and_peer(self):
            validate_seating_contacts(self.seatingContactPairs, self.seatingContactAllowanceM)
            validate_expected_contacts(self.expectedContactPairs)
            if self.qualifiedPlacement:
                if self.seatingContactPairs or len(self.fingerBodyKeys) != 2:
                    raise ValueError(
                        "Qualified joint safety needs two finger bodies and no legacy exceptions"
                    )
            elif self.expectedContactPairs or self.fingerBodyKeys:
                raise ValueError("Expected joint contacts require the explicit qualifiedPlacement opt-in")
            if self.lower >= self.upper or self.role != "axis" and not self.peerDoor:
                raise ValueError("Ordered limits and door peers are required")
            for values in (self.movingBodyKeys, self.clearanceBodyKeys):
                if len(values) != len(set(values)):
                    raise ValueError("Safety body declarations must be unique")
            if self.sweepLowerLocalM is not None and self.sweepUpperLocalM is not None:
                if any(a >= b for a, b in zip(self.sweepLowerLocalM, self.sweepUpperLocalM, strict=True)):
                    raise ValueError("Sweep bounds must have positive extent")
            return self

    def init(self, ctx):
        self.expected_contacts = validate_expected_contacts(ctx.config.expectedContactPairs)
        self.joint = ctx.physics.bind_joint(ctx.config.jointKey)
        self.handle, self.cancel_pending, self.failure = None, False, None
        self.body_ids, self.bounds, self.retained_payloads = {}, {}, set()
        self.bounds_error = None
        if ctx.config.role != "axis":
            # Immutable collider bounds can be prepared before the intake SLA.
            keys = {
                *ctx.config.clearanceBodyKeys,
                *ctx.config.movingBodyKeys,
                *ctx.config.attachedLids,
                *ctx.config.attachedLids.values(),
            }
            try:
                for key in keys:
                    self._bounds(ctx, key)
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                self.bounds_error = str(error)
        ctx.set(
            contactFactsAvailable=False,
            unsafeContactCount=0,
            lastContactShapes=[],
            lastContactDepthM=0.0,
            clearanceKnown=False,
            clearanceBlockedBy=[],
        )
        self.target = self.commanded = float(ctx.physics.joint_q(self.joint)[0])
        self.stable = self.elapsed = 0.0
        self._publish(ctx)

    def _body(self, ctx, key):
        if key not in self.body_ids:
            self.body_ids[key] = ctx.physics.bind_body(key)
        return self.body_ids[key]

    def _pose(self, ctx, key):
        position, quaternion = ctx.physics.body_pose(self._body(ctx, key))
        pose = np.r_[position, quaternion]
        if pose.shape != (7,) or not np.isfinite(pose).all():
            raise ValueError("Actual rigid pose is unavailable")
        rotation(pose[3:])
        return pose

    def _bounds(self, ctx, key):
        if key not in self.bounds:
            bounds = np.asarray(ctx.physics.collision_bounds(self._body(ctx, key)), dtype=float)
            if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or np.any(bounds[1] < bounds[0]):
                raise ValueError("Actual collider bounds are unknown")
            self.bounds[key] = bounds
        return self.bounds[key]

    def _payloads(self, ctx):
        if not ctx.config.armEntity:
            raise ValueError("Actual arm hold source is undeclared")
        key = ctx.read(ctx.config.armEntity, "holding")
        if key is None:
            return set()
        if not isinstance(key, str) or not key:
            raise ValueError("Actual held payload is unknown")
        result = {key}
        closed = ctx.read(key, "lidClosed")
        if closed is not True and closed is not False:
            raise ValueError("Actual payload lid state is unknown")
        if closed:
            lid = ctx.config.attachedLids.get(key)
            if not lid:
                raise ValueError("Closed carried lid body is undeclared")
            result.add(lid)
        return result

    def _safety(self, ctx):
        ctx.set(clearanceKnown=False, clearanceBlockedBy=[])
        try:
            if not ctx.config.movingBodyKeys:
                raise ValueError("Actual moving bodies are undeclared")
            if ctx.config.qualifiedPlacement:
                roots = {self._body(ctx, key) for key in ctx.config.movingBodyKeys}
                payloads = {
                    ctx.physics.body_key(body) for body in ctx.physics.welds.attached_bodies(roots) - roots
                }
            else:
                payloads = self._payloads(ctx)
            watched = self.retained_payloads | payloads
            keys = [*ctx.config.movingBodyKeys, *sorted(watched)]
            for key in keys:
                self._pose(ctx, key)
            bodies = {self._body(ctx, key) for key in keys}
            grasp = set()
            if ctx.config.qualifiedPlacement:
                bodies = ctx.physics.welds.attached_bodies(bodies)
                grasp = ctx.physics.welds.attached_bodies({self._body(ctx, k) for k in payloads})
                # The physical hand may have attached before holding facts publish.
                grasp |= bodies - {self._body(ctx, k) for k in ctx.config.movingBodyKeys}
            contacts = contacts_for_bodies(
                ctx.physics,
                bodies,
                limit_m=ctx.config.contactAllowanceM,
                seating_pairs=ctx.config.seatingContactPairs,
                seating_limit_m=ctx.config.seatingContactAllowanceM,
                on_seating_contact=(lambda **data: ctx.emit("seatingContact", **data))
                if ctx.config.seatingContactPairs
                else None,
                expected_pairs=self.expected_contacts,
                finger_bodies={self._body(ctx, k) for k in ctx.config.fingerBodyKeys},
                grasp_bodies=grasp,
                on_expected_contact=lambda **data: ctx.emit("expectedContact", **data),
            )
            if self.handle is not None:
                self.retained_payloads.update(payloads)
        except (AttributeError, KeyError, TypeError, ValueError):
            ctx.set(contactFactsAvailable=False, clearanceKnown=False, clearanceBlockedBy=[])
            return Reject(code="contact_unavailable", message="Actual moving-body contact facts are unknown")
        ctx.set(contactFactsAvailable=True)
        if contacts:
            worst = min(contacts, key=lambda row: row.distance_m)
            shapes, depth = list(worst.shapes), float(-worst.distance_m)
            ctx.set(
                unsafeContactCount=ctx.get("unsafeContactCount") + len(contacts),
                lastContactShapes=shapes,
                lastContactDepthM=depth,
            )
            ctx.emit("unsafeContact", shapes=shapes, depthM=depth)
            return Reject(
                code="unsafe_contact",
                message="Moving-body contact exceeded scene allowance",
                details={"shapes": shapes, "depthM": depth},
            )
        if ctx.config.role == "axis":
            ctx.set(clearanceKnown=True, clearanceBlockedBy=[])
            return None
        try:
            if (
                not ctx.config.sweepAnchorBody
                or ctx.config.sweepLowerLocalM is None
                or ctx.config.sweepUpperLocalM is None
                or not ctx.config.clearanceBodyKeys
            ):
                raise ValueError("Physical swept clearance is undeclared")
            if self.bounds_error:
                raise ValueError(self.bounds_error)
            keys = sorted(set(ctx.config.clearanceBodyKeys) | watched)
            frame = self._pose(ctx, ctx.config.sweepAnchorBody)
            actual = transformed_bounds(
                np.array([self._bounds(ctx, key) for key in keys]),
                np.array([self._pose(ctx, key) for key in keys]),
                frame,
            )
            current_door = transformed_bounds(
                np.array([self._bounds(ctx, key) for key in ctx.config.movingBodyKeys]),
                np.array([self._pose(ctx, key) for key in ctx.config.movingBodyKeys]),
                frame,
            )
            if not np.isfinite(actual).all():
                raise ValueError("Actual swept clearance is unknown")
            low = np.minimum(ctx.config.sweepLowerLocalM, current_door[:, 0].min(axis=0)) - CONTACT_LIMIT_M
            high = np.maximum(ctx.config.sweepUpperLocalM, current_door[:, 1].max(axis=0)) + CONTACT_LIMIT_M
            overlaps = np.all(actual[:, 1] >= low, axis=1) & np.all(actual[:, 0] <= high, axis=1)
            blocked = [key for key, overlap in zip(keys, overlaps, strict=True) if overlap]
        except (AttributeError, KeyError, TypeError, ValueError):
            ctx.set(clearanceKnown=False, clearanceBlockedBy=[])
            return Reject(code="unknown_clearance", message="Actual door swept clearance is unknown")
        ctx.set(clearanceKnown=True, clearanceBlockedBy=blocked)
        if blocked:
            return Reject(
                code="arm_in_door_sweep",
                message="Retract actual arm/payload from the door sweep",
                details={"bodies": blocked},
            )

    def _publish(self, ctx):
        q = float(ctx.physics.joint_q(self.joint)[0])
        qd = float(ctx.physics.joint_qd(self.joint)[0])
        ctx.set(
            position=q,
            velocity=qd,
            opened=abs(q - ctx.config.upper) < ctx.config.tolerance
            and abs(qd) < ctx.config.velocityTolerance,
            closed=abs(q - ctx.config.lower) < ctx.config.tolerance
            and abs(qd) < ctx.config.velocityTolerance,
            phase="idle" if self.handle is None else "stopping" if self.cancel_pending else "moving",
            controlSafe=self.handle is None and abs(qd) < ctx.config.velocityTolerance,
        )

    def _guard(self, inv, ctx):
        from packages.programs.cell_passage.guard import guard

        failed = guard(ctx, ctx.config.controlEntity, inv.params, [ctx.key])
        if failed:
            return failed
        role = ctx.config.role
        if inv.action == "stop":
            return None
        target = (
            ctx.config.upper
            if inv.action == "open"
            else ctx.config.lower
            if inv.action == "close"
            else inv.params["position"]
        )
        actual = float(ctx.physics.joint_q(self.joint)[0])
        peer_required = (
            role == "inner"
            and target > actual + ctx.config.tolerance
            or role == "outer"
            and target < actual - ctx.config.tolerance
        )
        if peer_required:
            needed = "opened" if role == "inner" else "closed"
            if (
                ctx.read(ctx.config.peerDoor, needed) is not True
                or ctx.read(ctx.config.peerDoor, "phase") != "idle"
            ):
                return Reject(
                    code="door_interlock", message="The peer door is not in the required settled pose"
                )

    def accept(self, inv, ctx):
        failed = self._guard(inv, ctx)
        if failed:
            return failed
        if inv.action == "move" and not ctx.config.lower <= inv.params["position"] <= ctx.config.upper:
            return Reject(code="joint_limit", message="Requested target exceeds fixed scene limits")
        if inv.action != "stop":
            failed = self._safety(ctx)
            if failed:
                self._hold(ctx)
                self._publish(ctx)
                return failed

    def _hold(self, ctx):
        self.target = self.commanded = float(ctx.physics.joint_q(self.joint)[0])
        ctx.physics.set_joint_target(self.joint, [self.target])
        ctx.physics.set_joint_target_velocity(self.joint, [0.0])

    def start(self, inv, ctx):
        failed = self.accept(inv, ctx)
        if failed:
            inv.fail(failed.code, failed.message)
            return
        if inv.action == "stop":
            if self.handle:
                self.handle.cancelled()
            self._hold(ctx)
        self.handle, self.cancel_pending, self.failure = inv, False, None
        if inv.action != "stop":
            if ctx.config.qualifiedPlacement:
                roots = {self._body(ctx, key) for key in ctx.config.movingBodyKeys}
                self.retained_payloads = {
                    ctx.physics.body_key(body) for body in ctx.physics.welds.attached_bodies(roots) - roots
                }
            else:
                self.retained_payloads = self._payloads(ctx)
            ctx.set(unsafeContactCount=0, lastContactShapes=[], lastContactDepthM=0.0)
        self.elapsed = self.stable = 0.0
        if inv.action != "stop":
            self.commanded = float(ctx.physics.joint_q(self.joint)[0])
            self.target = (
                ctx.config.upper
                if inv.action == "open"
                else ctx.config.lower
                if inv.action == "close"
                else inv.params["position"]
            )
        self.deadline = abs(self.target - self.commanded) / ctx.config.maxSpeed * 2 + 3
        self._publish(ctx)

    def cancel(self, inv, ctx):
        if self.handle and inv.id == self.handle.id:
            self.cancel_pending = True
            self._hold(ctx)

    def tick(self, ctx, dt):
        inv = self.handle
        if inv is None:
            self._publish(ctx)
            return
        failed = self._guard(inv, ctx)
        safety = self._safety(ctx)
        failed = failed or safety
        if failed:
            self.failure = self.failure or failed
            if not self.cancel_pending:
                self._hold(ctx)
            self.cancel_pending = True
        self.elapsed += dt
        if self.elapsed > self.deadline and not self.cancel_pending:
            self.failure = Reject(code="motion_timeout", message="Actual scalar joint did not settle")
            self.cancel_pending = True
            self._hold(ctx)
        distance = ctx.config.maxSpeed * dt
        self.commanded += max(-distance, min(distance, self.target - self.commanded))
        ctx.physics.set_joint_target(self.joint, [self.commanded])
        ctx.physics.set_joint_target_velocity(self.joint, [0.0])
        q, qd = float(ctx.physics.joint_q(self.joint)[0]), float(ctx.physics.joint_qd(self.joint)[0])
        settled = abs(q - self.target) < ctx.config.tolerance and abs(qd) < ctx.config.velocityTolerance
        self.stable = self.stable + dt if settled else 0.0
        if self.stable >= 0.1:
            if self.failure:
                inv.fail(self.failure.code, self.failure.message)
            elif self.cancel_pending:
                inv.cancelled()
            else:
                inv.succeed()
            self.handle = None
            self.retained_payloads.clear()
        self._publish(ctx)
