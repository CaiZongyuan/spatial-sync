"""Flex actuator owner and atomic, recorded material/control state.

Liquid is a symbolic model driven by measured pump displacement, never elapsed
time or the requested target. PhysicsPort supplies the mechanical observations.
"""

import json
import math
from numbers import Real

import numpy as np
from pydantic import ConfigDict, Field, model_validator

from packages.info.program import ProgramConfig, Reject

from .guard import guard
from .tip_pickup import TipGeometry, check_pickup

LIQUID_ACTIONS = {"pickupTip", "discardTip", "aspirate", "dispense", "mix", "detach"}
RECENT_OPERATIONS = 64
QUANTITY_ACTIONS = {
    "dispenseAll": "dispense",
    "aspirateAvailable": "aspirate",
    "aspirateFraction": "aspirate",
    "aspirateTopUp": "aspirate",
}


def base_action(action):
    return QUANTITY_ACTIONS.get(action, action)


class MaterialConfig(ProgramConfig):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Vessel(MaterialConfig):
    id: str
    entityKey: str
    site: str
    capacityUl: float = Field(gt=0)
    volumeUl: float = Field(default=0, ge=0)
    reagent: str = ""
    culture: str = ""
    parent: str = ""
    live: float = Field(default=0, ge=0)
    dead: float = Field(default=0, ge=0)
    attached: bool = False
    waste: bool = False
    lidProperty: str = ""
    requiredStation: str = ""

    @model_validator(mode="after")
    def valid_initial_content(self):
        if self.volumeUl > self.capacityUl or ((self.live or self.dead) and not self.culture):
            raise ValueError("Initial contents must fit and cells require a culture identity")
        return self


class Tip(MaterialConfig):
    id: str
    site: str
    bodyKey: str


def empty_content():
    return {"volumeUl": 0.0, "components": [], "cells": []}


def content_origins(content):
    return {
        cell["culture"]
        for cell in content["cells"]
        if sum(value for key, value in cell.items() if key not in ("culture", "parent")) > 1e-8
    }


def move_content(source, target, amount):
    """Move only homogeneous suspended material; attached/pelleted cells remain."""
    fraction = amount / source["volumeUl"] if source["volumeUl"] else 0
    for component in source["components"]:
        moved = component["volumeUl"] * fraction
        component["volumeUl"] -= moved
        existing = next((row for row in target["components"] if row["name"] == component["name"]), None)
        if existing is None:
            existing = {"name": component["name"], "volumeUl": 0.0}
            target["components"].append(existing)
        existing["volumeUl"] += moved
    for cell in source["cells"]:
        existing = next((row for row in target["cells"] if row["culture"] == cell["culture"]), None)
        if existing is None:
            existing = {key: (cell[key] if key in ("culture", "parent") else 0.0) for key in cell}
            target["cells"].append(existing)
        for key in ("suspendedLive", "suspendedDead"):
            moved = cell[key] * fraction
            cell[key] -= moved
            existing[key] += moved
    source["volumeUl"] -= amount
    target["volumeUl"] += amount


class WorkcellProgram:
    id, type, tick_priority = "cell_passage.workcell", "cell-passage-workcell/v1", 5

    class Config(MaterialConfig):
        contactAllowanceM: float = Field(default=.001, gt=0, le=.002)
        axisJoints: list[str] = Field(
            min_length=3, max_length=3, json_schema_extra={"spatialReference": "joint"}
        )
        pumpJoint: str = Field(json_schema_extra={"spatialReference": "joint"})
        tipSite: str = Field(json_schema_extra={"spatialReference": "site"})
        pipetteBody: str = Field(json_schema_extra={"spatialReference": "body"})
        movingBodyKeys: list[str] = Field(
            default_factory=list, json_schema_extra={"spatialReference": "body"}
        )
        vessels: list[Vessel]
        tips: list[Tip]
        tipGeometry: TipGeometry | None = Field(default=None, exclude_if=lambda value: value is None)
        contentSites: list[str] = Field(json_schema_extra={"spatialReference": "site"})
        tipRackSites: list[str] = Field(json_schema_extra={"spatialReference": "site"})
        cabinetEntity: str
        managedDevices: list[str] = []
        pumpUlPerUnit: float = Field(default=100000, gt=0)
        tipCapacityUl: float = Field(default=1000, gt=0)
        alignmentToleranceM: float = Field(default=0.003, gt=0)
        axisToleranceM: float = Field(default=0.0005, gt=0)
        # Teaching simulation drive limits, not OEM performance specifications.
        xySpeedMPerS: float = Field(default=0.2, gt=0)
        zSpeedMPerS: float = Field(default=0.05, gt=0)
        axisAccelerationMPerS2: float = Field(default=0.25, gt=0)
        holdSpeedTolerance: float = Field(default=0.0001, gt=0)
        pumpToleranceUl: float = Field(default=0.01, gt=0)
        wasteSite: str | None = Field(
            default=None,
            exclude_if=lambda value: value is None,
            json_schema_extra={"spatialReference": "site"},
        )
        detachSeconds: float = Field(default=2, gt=0)
        imagerStation: str = ""
        centrifugeSeconds: float = Field(default=2, gt=0)

        @model_validator(mode="after")
        def unique_compartments(self):
            for rows in (self.vessels, self.tips):
                if len({row.id for row in rows}) != len(rows):
                    raise ValueError("Compartment and tip identities must be unique")
            for sites, rows in ((self.contentSites, self.vessels), (self.tipRackSites, self.tips)):
                if len(sites) != len(set(sites)) or set(sites) != {row.site for row in rows}:
                    raise ValueError(
                        "Top-level Site references must exactly cover the nested content/tip Sites"
                    )
            return self

    def init(self, ctx):
        self.axes = [ctx.physics.bind_joint(key) for key in ctx.config.axisJoints]
        self.pump = ctx.physics.bind_joint(ctx.config.pumpJoint)
        self.last_good_q = {joint: ctx.physics.joint_q(joint).copy() for joint in [*self.axes, self.pump]}
        self.tip_site = ctx.physics.bind_site(ctx.config.tipSite)
        self.pipette_body = ctx.physics.bind_body(ctx.config.pipetteBody)
        self.moving_bodies = {ctx.physics.bind_body(key) for key in ctx.config.movingBodyKeys}
        self.contact_blocked = True
        self.contact_hit = False
        self.tip_bodies = {row.id: ctx.physics.bind_body(row.bodyKey) for row in ctx.config.tips}
        self.configs = {row.id: row for row in ctx.config.vessels}
        self.sites = {row.id: ctx.physics.bind_site(row.site) for row in ctx.config.vessels}
        self.content_site_groups = {row.id: ctx.physics.bind_sites([row.site]) for row in ctx.config.vessels}
        self.waste_sites = ctx.physics.bind_sites([ctx.config.wasteSite]) if ctx.config.wasteSite else None
        self.tip_sites = {row.id: ctx.physics.bind_site(row.site) for row in ctx.config.tips}
        self.tip_docks = {row.id: ctx.physics.bind_sites([row.site]) for row in ctx.config.tips}
        self.handle, self.stroke, self.pending = None, None, None
        self.motion = None
        self.stopping = False
        self.hold_targets = None
        self.stop_error = None
        self.quantity = None
        self.completing = False
        self.quantity_previous_q, self.quantity_stable_samples = None, 0
        self.quantity_previous_frame = None
        self.cancelled_at_tick = None
        self.used_operations = getattr(self, "used_operations", set())
        self.control_receipts = getattr(self, "control_receipts", {})
        self.control_generation = getattr(self, "control_generation", -1) + 1
        vessels = []
        for config in ctx.config.vessels:
            row = empty_content() | {
                "id": config.id,
                "entityKey": config.entityKey,
                "capacityUl": config.capacityUl,
                "culture": config.culture,
                "parent": config.parent,
                "waste": config.waste,
                "mixed": True,
                "exposureSince": -1.0,
                "processedCycle": "",
            }
            row["volumeUl"] = config.volumeUl
            if config.volumeUl:
                row["components"] = [{"name": config.reagent or "medium", "volumeUl": config.volumeUl}]
            if config.culture and (config.live or config.dead):
                row["cells"] = [
                    {
                        "culture": config.culture,
                        "parent": config.parent,
                        "attachedLive": config.live if config.attached else 0.0,
                        "attachedDead": config.dead if config.attached else 0.0,
                        "suspendedLive": 0.0 if config.attached else config.live,
                        "suspendedDead": 0.0 if config.attached else config.dead,
                        "pelletLive": 0.0,
                        "pelletDead": 0.0,
                    }
                ]
            vessels.append(row)
        self.state = {
            "revision": 0,
            "reconciled": True,
            "vessels": vessels,
            "tips": [{"id": row.id, "state": "unused"} for row in ctx.config.tips],
            "tip": empty_content() | {"id": "", "contactCulture": "", "contactWaste": False},
            "reservations": [],
            "operations": [],
            "observations": [],
            "controlRequests": [],
            "operationCount": 0,
            "terminalOperationCount": 0,
            "archivedOperationCount": 0,
        }
        self.initial_volume = sum(row["volumeUl"] for row in vessels)
        self.initial_cells = self._cell_totals()
        ctx.set(
            controlOwner="auto",
            controlEpoch=self.control_generation,
            controlMode="auto",
            controlSafe=True,
            controlSafeAtSimTime=None,
            phase="idle",
            materialSnapshot=self.state,
            materialRevision=0,
            auditPassed=True,
            lastOperationId="",
            lastAppliedUl=0.0,
            remainingUl=0.0,
            observationRevision=0,
            inFlightUl=0.0,
            tipsRemaining=len(ctx.config.tips),
            wasteFreeUl=sum(row["capacityUl"] - row["volumeUl"] for row in vessels if row["waste"]),
            samplesIndependent=True,
            lastObservedLive=0.0,
            lastObservedDead=0.0,
            operationCount=0,
            terminalOperationCount=0,
            archivedOperationCount=0,
            lastResolvedUl=0.0,
            lastResolutionResidualUl=0.0,
            lastUntransferredUl=0.0,
            lastQuantityOutcome="not_applicable",
            contactFactsAvailable=False,
            unsafeContactCount=0,
            lastContactShapes=[],
            lastContactDepthM=0.0,
        )
        self._contact_failure(ctx)
        self._observe_safety(ctx, not self.contact_blocked and self._stationary(ctx))

    def _observe_safety(self, ctx, safe, **values):
        """Publish only with a just-evaluated contact/standstill predicate."""
        sampled = getattr(ctx.physics, "last_time", ctx.sim_time)
        if (ctx.get("contactFactsAvailable") is not True or isinstance(sampled, bool)
                or not isinstance(sampled, Real) or not math.isfinite(sampled) or sampled < 0):
            sampled = None
        else:
            sampled = float(sampled)
        ctx.set(controlSafe=safe, controlSafeAtSimTime=sampled, **values)
        return sampled

    def _publish(self, ctx):
        self.state["revision"] += 1
        total = sum(row["volumeUl"] for row in self.state["vessels"]) + self.state["tip"]["volumeUl"]
        cell_totals = self._cell_totals()
        balanced = (
            self.state["reconciled"]
            and abs(total - self.initial_volume) < 1e-6
            and all(
                abs(value - cell_totals.get(culture, 0)) < 1e-6
                for culture, value in self.initial_cells.items()
            )
        )
        independent = all(len(content_origins(row)) <= 1 for row in self.state["vessels"] if not row["waste"])
        contents_valid = all(
            -1e-6 <= row["volumeUl"] <= row.get("capacityUl", ctx.config.tipCapacityUl) + 1e-6
            and abs(row["volumeUl"] - sum(c["volumeUl"] for c in row["components"])) < 1e-6
            and all(
                value >= -1e-6
                for cell in row["cells"]
                for key, value in cell.items()
                if key not in ("culture", "parent")
            )
            for row in [*self.state["vessels"], self.state["tip"]]
        )
        ctx.set(
            materialSnapshot=self.state,
            materialRevision=self.state["revision"],
            auditPassed=balanced and independent and contents_valid,
            samplesIndependent=independent,
            inFlightUl=self.state["tip"]["volumeUl"],
            tipsRemaining=sum(row["state"] == "unused" for row in self.state["tips"]),
            wasteFreeUl=sum(
                row["capacityUl"] - row["volumeUl"] for row in self.state["vessels"] if row["waste"]
            ),
            operationCount=self.state["operationCount"],
            terminalOperationCount=self.state["terminalOperationCount"],
            archivedOperationCount=self.state["archivedOperationCount"],
        )

    def _cell_totals(self):
        totals = {}
        for row in [*self.state["vessels"], self.state["tip"]]:
            for cell in row["cells"]:
                totals[cell["culture"]] = totals.get(cell["culture"], 0.0) + sum(
                    value for key, value in cell.items() if key not in ("culture", "parent")
                )
        return totals

    def _vessel(self, name):
        return next((row for row in self.state["vessels"] if row["id"] == name), None)

    def _aligned(self, ctx, site, sites=None):
        try:
            tip = self.state["tip"]["id"]
            if tip:
                # Same-source tip Root is its fluid endpoint; the compliant
                # carried body can differ from the nominal head Site.
                body = self.tip_bodies[tip]
                position, quaternion = (np.asarray(x) for x in ctx.physics.body_pose(body))
                velocity = np.asarray(ctx.physics.relative_site_velocity(body, sites))
                if (
                    velocity.shape != (1, 3)
                    or not np.isfinite(velocity).all()
                    or np.linalg.norm(velocity[0]) > ctx.config.holdSpeedTolerance
                ):
                    return False
            else:
                position, quaternion = (np.asarray(x) for x in ctx.physics.site_pose(self.tip_site))
            target = np.asarray(ctx.physics.site_pose(site)[0])
            return (
                position.shape == target.shape == (3,)
                and quaternion.shape == (4,)
                and np.isfinite(position).all()
                and np.isfinite(target).all()
                and np.isfinite(quaternion).all()
                and abs(np.linalg.norm(quaternion) - 1) <= 1e-3
                and np.linalg.norm(position - target) <= ctx.config.alignmentToleranceM
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            return False

    def _vessel_conditions(self, ctx, name):
        config = self.configs[name]
        if config.lidProperty and ctx.read(config.entityKey, config.lidProperty) is not False:
            return Reject(code="closed_vessel", message="Vessel lid must be confirmed open")
        if config.requiredStation and (
            ctx.read(config.entityKey, "station") != config.requiredStation
            or ctx.read(config.entityKey, "settled") is not True
        ):
            return Reject(code="wrong_station", message="Vessel is not settled at its operation station")

    def _accessible(self, ctx, name):
        failed = self._vessel_conditions(ctx, name)
        if failed:
            return failed
        if not self._aligned(ctx, self.sites[name], self.content_site_groups[name]):
            return Reject(code="not_aligned", message="Actual pipette tip is not aligned with the vessel")

    def _contact_failure(self, ctx):
        was_hit = self.contact_hit
        try:
            if not self.moving_bodies:
                raise ValueError("Moving body ownership was not declared")
            active = self.moving_bodies.copy()
            if self.state["tip"]["id"]:
                active.add(self.tip_bodies[self.state["tip"]["id"]])
            contacts = ctx.physics.collision_contacts(max_distance=-ctx.config.contactAllowanceM)
            hit = None
            for row in contacts:
                if (
                    len(row.bodies) != 2
                    or len(row.shapes) != 2
                    or not all(isinstance(shape, str) and shape for shape in row.shapes)
                    or not np.isfinite(row.distance_m)
                ):
                    raise ValueError("Unknown contact identity or distance")
                if (row.distance_m < -ctx.config.contactAllowanceM
                        and any(body in active for body in row.bodies)):
                    hit = row
                    break
        except (AttributeError, KeyError, TypeError, ValueError):
            self.contact_blocked = True
            self.contact_hit = False
            ctx.set(contactFactsAvailable=False, controlSafe=False)
            return Reject(code="contact_unavailable", message="Actual Flex/payload contacts are unavailable")
        ctx.set(contactFactsAvailable=True)
        self.contact_blocked = hit is not None
        self.contact_hit = hit is not None
        if hit is not None:
            shapes, depth = list(hit.shapes), -float(hit.distance_m)
            ctx.set(lastContactShapes=shapes, lastContactDepthM=depth, controlSafe=False)
            if not was_hit:
                ctx.set(unsafeContactCount=ctx.get("unsafeContactCount") + 1)
                ctx.emit("unsafeContact", shapes=shapes, depthM=depth)
            return Reject(code="unsafe_contact", message="Actual Flex or held tip exceeded scene allowance")

    def _reject(self, code, message):
        return Reject(code=code, message=message)

    def _cabinet_ready(self, ctx):
        try:
            return ctx.read(ctx.config.cabinetEntity, "cabinetReady") is True
        except KeyError:
            return False

    def _top_up_target(self, inv, ctx, source):
        target = self._vessel(inv.params["targetVessel"])
        if target is None or target["id"] == source["id"] or target["waste"]:
            return self._reject("invalid_target", "Top-up needs a different experimental target vessel")
        failed = guard(ctx, ctx.key, inv.params, [target["entityKey"]]) or self._vessel_conditions(
            ctx, target["id"]
        )
        if failed:
            return failed
        if inv.params["targetVolumeUl"] > target["capacityUl"]:
            return self._reject("capacity_exceeded", "Top-up goal exceeds the target vessel capacity")
        tip = self.state["tip"]
        origins = content_origins(source) | content_origins(tip) | content_origins(target)
        if tip["contactCulture"]:
            origins.add(tip["contactCulture"])
        if origins - {target["parent"] or target["culture"]}:
            return self._reject("cross_sample", "Top-up source, tip and target cultures are incompatible")
        return target

    def _quantity_plan(self, inv, ctx, row):
        p, action, tip = inv.params, base_action(inv.action), self.state["tip"]
        q = float(ctx.physics.joint_q(self.pump)[0])
        direction = -1 if action == "dispense" else 1
        requested = p.get("volumeUl", tip["volumeUl"])
        plan = {
            "quantityKind": "literal",
            "requestedUl": requested,
            "resolvedUl": requested,
            "resolutionResidualUl": 0.0,
            "resolutionUl": 0.0,
            "pumpStartM": q,
            "pumpTargetM": q + direction * requested / ctx.config.pumpUlPerUnit,
            "quantityOutcome": "resolved",
            "remainderLocation": "tip" if action == "dispense" else row["id"],
        }
        if inv.action not in QUANTITY_ACTIONS:
            return plan
        kind, empty_outcome = "allTip", "empty_tip"
        if inv.action == "aspirateAvailable":
            requested = max(0.0, row["volumeUl"] - p.get("reserveUl", 0.0))
            kind, empty_outcome = (
                "available",
                "at_or_below_reserve" if p.get("reserveUl", 0) else "empty_source",
            )
        elif inv.action == "aspirateFraction":
            requested = row["volumeUl"] * p["fraction"]
            kind, empty_outcome = "fraction", "empty_source"
        elif inv.action == "aspirateTopUp":
            target = self._top_up_target(inv, ctx, row)
            if isinstance(target, Reject):
                return target
            deficit = max(0.0, p["targetVolumeUl"] - target["volumeUl"])
            if tip["volumeUl"] > deficit + 1e-8:
                return self._reject(
                    "tip_exceeds_target", "Existing tip liquid already exceeds the target deficit"
                )
            requested = max(0.0, deficit - tip["volumeUl"])
            kind, empty_outcome = "topUp", "at_target" if not deficit else "tip_sufficient"
        plan.update(quantityKind=kind, requestedUl=requested)
        if not self._stationary(ctx):
            return self._reject("not_safe", "Settle the mechanism before resolving a quantity intent")
        try:
            lower, upper = ctx.physics.joint_limits(self.pump)
            lower, upper = (
                np.asarray(lower, dtype=float).reshape(-1),
                np.asarray(upper, dtype=float).reshape(-1),
            )
            if lower.size != 1 or upper.size != 1:
                raise ValueError("Quantity intents need a scalar pump")
            low, high = float(lower[0]), float(upper[0])
        except (AttributeError, KeyError, TypeError, ValueError):
            return self._reject("pump_limits_unavailable", "Actual scalar pump limits must be known")
        if not np.isfinite([q, low, high]).all() or low >= high:
            return self._reject("pump_limits_unavailable", "Actual finite pump limits must be known")
        quantum_m = abs(float(np.spacing(np.float32(max(abs(low), abs(high), abs(q))))))
        quantum_ul = quantum_m * ctx.config.pumpUlPerUnit
        if not np.isfinite(quantum_ul) or quantum_ul <= 0 or quantum_ul > ctx.config.pumpToleranceUl:
            return self._reject(
                "quantity_resolution", "Pump coordinate resolution cannot satisfy this tolerance"
            )
        if q < low - quantum_m or q > high + quantum_m:
            return self._reject("pump_out_of_bounds", "Actual pump position is outside its known limits")
        source = tip if action == "dispense" else row
        capacity = (
            row["capacityUl"] - row["volumeUl"]
            if action == "dispense"
            else ctx.config.tipCapacityUl - tip["volumeUl"]
        )
        physical = (q - low if direction == -1 else high - q) * ctx.config.pumpUlPerUnit
        if requested > source["volumeUl"] + 1e-8:
            return self._reject("insufficient_liquid", "Quantity intent exceeds actual source stock")
        if requested - max(0, capacity) > ctx.config.pumpToleranceUl:
            return self._reject("capacity_exceeded", "Quantity intent exceeds physical destination capacity")
        if requested - max(0, physical) > ctx.config.pumpToleranceUl:
            return self._reject("pump_capacity", "Quantity intent exceeds actual pump travel")
        feasible = max(0.0, min(requested, source["volumeUl"], capacity, physical))
        # Leave one native coordinate quantum on the safe side of the liquid
        # boundary, then round toward the starting position. No liquid is rounded.
        safe_target = q + direction * (feasible / ctx.config.pumpUlPerUnit - quantum_m)
        if direction * (safe_target - q) <= 0:
            target = q
        else:
            target = float(np.float32(safe_target))
            if direction * (target - safe_target) > 0:
                target = float(
                    np.nextafter(np.float32(target), np.float32(-np.inf if direction == 1 else np.inf))
                )
        resolved = max(0.0, direction * (target - q) * ctx.config.pumpUlPerUnit)
        residual = max(0.0, requested - resolved)
        if residual > ctx.config.pumpToleranceUl:
            return self._reject("quantity_resolution", "Untransferable residue exceeds the fixed tolerance")
        if resolved == 0:
            target = q
        if resolved > 0 and not low <= target <= high:
            return self._reject("quantity_resolution", "Representable target is outside actual pump limits")
        plan.update(
            resolvedUl=resolved,
            resolutionResidualUl=residual,
            resolutionUl=quantum_ul,
            pumpTargetM=target,
            quantityOutcome="resolved"
            if resolved > 0
            else "below_resolution"
            if requested
            else empty_outcome,
        )
        return plan

    def accept(self, inv, ctx):
        p, action = inv.params, base_action(inv.action)
        if p["operationId"] in self.used_operations:
            return self._reject(
                "operation_applied", "This domain operation has already been applied; use current remainder"
            )
        if action in ("requestControl", "confirmControl"):
            return self._control(inv, ctx)
        if p["operationId"] in self.control_receipts:
            return self._reject(
                "operation_applied", "This domain identity belongs to an applied control request"
            )
        rejection = guard(ctx, ctx.key, p)
        if rejection:
            return rejection
        failed = self._contact_failure(ctx)
        if failed:
            return failed
        if self.stopping:
            return self._reject(
                "not_safe", "Wait for the previous operation to settle and finish material accounting"
            )
        if not self.state["reconciled"]:
            return self._reject(
                "material_unreconciled",
                "Actuator feedback invalidated the material estimate; preserve this interrupted run",
            )
        if action in LIQUID_ACTIONS and not self._cabinet_ready(ctx):
            return self._reject(
                "cabinet_not_ready", "Cabinet readiness must be confirmed before liquid handling"
            )
        if action == "reserve":
            if p["device"] not in [ctx.key, *ctx.config.managedDevices]:
                return self._reject(
                    "unknown_device", "Reservations may be assigned only to configured workcell devices"
                )
            for row in self.state["reservations"]:
                if row["resource"] == p["resource"] and row["device"] != p["device"]:
                    return self._reject("resource_busy", "Resource is already reserved by another device")
        if action == "release":
            if self.handle or self.state["tip"]["volumeUl"] > 1e-8 or not self._stationary(ctx):
                return self._reject(
                    "unsafe_release", "Stop motion and resolve in-flight liquid before release"
                )
            if any(ctx.read(key, "controlSafe") is not True for key in ctx.config.managedDevices):
                return self._reject("unsafe_release", "A managed device has not established safe hold")
            for key in ctx.config.managedDevices:
                try:
                    holding = ctx.read(key, "holding")
                except KeyError:
                    continue  # Doors and centrifuges have no gripper holding property.
                if holding == p["resource"]:
                    return self._reject("unsafe_release", "The resource is still physically held by a device")
        if action == "pickupTip":
            tip = next((row for row in self.state["tips"] if row["id"] == p["tip"]), None)
            if self.state["tip"]["id"] or tip is None or tip["state"] != "unused":
                return self._reject("tip_unavailable", "A fresh tip and an empty pipette are required")
            if not self._aligned(ctx, self.tip_sites[p["tip"]]):
                return self._reject("not_aligned", "Pipette is not at the selected actual tip position")
            if abs(float(ctx.physics.joint_q(self.pump)[0])) > 1e-7:
                return self._reject(
                    "pump_not_zero", "Return empty pipette piston to zero before fitting a tip"
                )
            if not self._stationary(ctx):
                return self._reject("not_safe", "Measured head/pump standstill is required before tip pickup")
            failed = check_pickup(
                ctx.physics,
                ctx.config.tipGeometry,
                self.tip_bodies[p["tip"]],
                self.tip_docks[p["tip"]],
                self.tip_site,
                ctx.config.alignmentToleranceM,
            )
            if failed:
                return failed
        if action == "discardTip":
            if not self.state["tip"]["id"]:
                return self._reject("tip_unavailable", "No tip is installed")
            if not ctx.config.wasteSite or not self._aligned(
                ctx, ctx.physics.bind_site(ctx.config.wasteSite), self.waste_sites
            ):
                return self._reject("not_aligned", "Move to the solid waste position before discarding")
            waste = self._vessel(p["waste"])
            if (
                not waste
                or not waste["waste"]
                or waste["volumeUl"] + self.state["tip"]["volumeUl"] > waste["capacityUl"]
            ):
                return self._reject("waste_full", "A waste compartment with sufficient capacity is required")
        if action in ("aspirate", "dispense", "mix", "detach", "observe", "count"):
            row = self._vessel(p["vessel"])
            if row is None:
                return self._reject("unknown_vessel", "Unknown content compartment")
            if action in ("observe", "count"):
                config = self.configs[row["id"]]
                if (
                    not ctx.config.imagerStation
                    or ctx.read(config.entityKey, "station") != ctx.config.imagerStation
                    or ctx.read(config.entityKey, "settled") is not True
                ):
                    return self._reject(
                        "imager_not_loaded", "Observation requires the actual settled vessel in the imager"
                    )
            else:
                rejection = self._accessible(ctx, row["id"])
                if rejection:
                    return rejection
            rejection = guard(ctx, ctx.key, p, [self.configs[row["id"]].entityKey])
            if rejection:
                return rejection
            if action in ("aspirate", "dispense", "mix") and not self.state["tip"]["id"]:
                return self._reject("tip_unavailable", "Install a fresh tip before liquid handling")
            if action in ("aspirate", "dispense", "mix"):
                tip = self.state["tip"]
                quantity = self._quantity_plan(inv, ctx, row)
                if isinstance(quantity, Reject):
                    return quantity
                amount = quantity["resolvedUl"]
                if (row["waste"] and action in ("aspirate", "mix")) or (
                    tip["contactWaste"] and not (row["waste"] and action == "dispense")
                ):
                    return self._reject(
                        "waste_return_forbidden",
                        "Waste and waste-contact tips cannot return material to the experiment",
                    )
                source, destination = (tip, row) if action == "dispense" else (row, tip)
                capacity = row["capacityUl"] if action == "dispense" else ctx.config.tipCapacityUl
                if action == "mix" and tip["volumeUl"] > 1e-8:
                    return self._reject("tip_not_empty", "Mixing requires an empty tip")
                if source["volumeUl"] + 1e-8 < amount:
                    return self._reject(
                        "insufficient_liquid", "Requested transfer exceeds actual available liquid"
                    )
                if destination["volumeUl"] + amount > capacity + 1e-8:
                    return self._reject(
                        "capacity_exceeded", "Requested transfer exceeds destination capacity"
                    )
                expected = row["parent"] or row["culture"]
                origins = content_origins(row)
                tip_origins = content_origins(tip) | (
                    {tip["contactCulture"]} if tip["contactCulture"] else set()
                )
                if not row["waste"] and ((origins - {expected}) or (tip_origins - {expected})):
                    return self._reject(
                        "cross_sample",
                        "Contents or tip contact do not match this culture; keep reagent stocks cell-free",
                    )
                if (
                    not row["mixed"]
                    and action == "aspirate"
                    and any(c["suspendedLive"] or c["suspendedDead"] for c in row["cells"])
                ):
                    return self._reject("not_mixed", "Resuspend before transferring suspended cells")
            if action == "detach" and (
                row["exposureSince"] < 0 or ctx.sim_time - row["exposureSince"] < ctx.config.detachSeconds
            ):
                return self._reject("exposure_incomplete", "Teaching dissociation exposure is not complete")
        if action == "pellet":
            centrifuge = p["centrifuge"]
            try:
                completed_cycle = ctx.read(centrifuge, "completedCycle")
            except KeyError:
                return self._reject(
                    "unknown_device", "The requested device does not publish centrifuge cycle evidence"
                )
            if (
                completed_cycle != p["cycle"]
                or ctx.read(centrifuge, "phase") != "idle"
                or (ctx.read(centrifuge, "cycleRunSeconds") or 0) < ctx.config.centrifugeSeconds
                or (ctx.read(centrifuge, "actualRotorTurns") or 0) <= 0
            ):
                return self._reject(
                    "cycle_incomplete", "A completed actual rotor cycle with teaching duration is required"
                )
            load = ctx.read(centrifuge, "completedLoad") or []
            for name in p["vessels"]:
                row = self._vessel(name)
                if row is None or self.configs[name].entityKey not in load:
                    return self._reject("wrong_load", "Vessel did not participate in this completed cycle")
                if row["processedCycle"] == p["cycle"]:
                    return self._reject(
                        "cycle_applied", "This completed cycle was already applied to the vessel"
                    )
            resources = {centrifuge, *(self.configs[name].entityKey for name in p["vessels"])}
            for resource in resources:
                if not any(
                    lease["resource"] == resource
                    and lease["device"] == centrifuge
                    and lease["owner"] == p["owner"]
                    and lease["epoch"] == p["epoch"]
                    for lease in self.state["reservations"]
                ):
                    return self._reject(
                        "reservation_required",
                        "Retain centrifuge and processed-vessel reservations through material application",
                    )
        return None

    def _control(self, inv, ctx):
        p = inv.params
        old = self.control_receipts.get(p["operationId"])
        if old:
            if old["action"] != inv.action or old["paramsJson"] != json.dumps(p, sort_keys=True):
                return self._reject(
                    "operation_conflict",
                    "A control domain identity cannot be reused for different parameters",
                )
            return None
        if p["epoch"] != ctx.get("controlEpoch") or p["owner"] != ctx.get("controlOwner"):
            return self._reject("stale_control", "Control request belongs to an older owner or epoch")
        if inv.action == "confirmControl":
            failed = self._contact_failure(ctx)
            if failed:
                return failed
            previously_safe = ctx.get("controlSafe") is True
            stationary = self._stationary(ctx)
            sampled = self._observe_safety(ctx, not self.contact_blocked and stationary)
            details = {"controlSafeAtSimTime": sampled} if sampled is not None else {}
            if (
                ctx.get("controlMode") != "holding"
                or not previously_safe
                or not stationary
            ):
                return Reject(code="not_safe", message="Workcell has not established safe hold",
                              details=details)
            if any(ctx.read(key, "controlSafe") is not True for key in ctx.config.managedDevices):
                return Reject(code="not_safe", message="A managed device has not established safe hold",
                              details=details)
        elif ctx.get("controlMode") == "holding":
            return self._reject("handoff_pending", "Confirm the current handoff before requesting another")
        if inv.action == "requestControl":
            self._contact_failure(ctx)
            self.control_generation += 1
            ctx.set(controlEpoch=self.control_generation, controlMode="holding", controlSafe=False)
            self.pending = {"owner": p["nextOwner"], "mode": p["mode"]}
            self.completing = False
            self.cancelled_at_tick = None
            self._hold(ctx)
        else:
            ctx.set(controlOwner=self.pending["owner"], controlMode=self.pending["mode"])
            self.state["reservations"] = [
                r | {"owner": self.pending["owner"], "epoch": ctx.get("controlEpoch")}
                for r in self.state["reservations"]
            ]
            self.pending = None
        receipt = {
            "operationId": p["operationId"],
            "epoch": ctx.get("controlEpoch"),
            "mode": ctx.get("controlMode"),
            "owner": ctx.get("controlOwner"),
            "action": inv.action,
            "paramsJson": json.dumps(p, sort_keys=True),
        }
        self.state["controlRequests"].append(receipt)
        self.control_receipts[p["operationId"]] = receipt
        self._publish(ctx)
        return None

    def _hold(self, ctx, failure=None):
        # Invalidated motion must never restore its old endpoint after a pause.
        self.motion = None
        completed_success = False
        if failure is not None:
            self.stop_error = failure
            if self.handle and self.state["operations"][-1]["fault"] != failure[0]:
                self.state["operations"][-1]["fault"] = failure[0]
                self._publish(ctx)
        # Capture already executed stroke before replacing the old drive target.
        if self.stroke:
            self._apply_stroke(ctx)
        if not self.stopping:
            self.hold_targets = {}
            for joint in [*self.axes, self.pump]:
                measured = ctx.physics.joint_q(joint)
                if np.isfinite(measured).all():
                    self.last_good_q[joint] = measured.copy()
                ctx.physics.set_joint_target(joint, self.last_good_q[joint])
                self.hold_targets[joint] = self.last_good_q[joint].copy()
                ctx.physics.set_joint_target_velocity(joint, [0])
            self.stopping = True
            if self.handle and self.state["reconciled"]:
                self.state["operations"][-1]["status"] = "settling"
                self._publish(ctx)
        close_allowed = self.cancelled_at_tick is None or ctx.tick_index > self.cancelled_at_tick
        if self.handle and close_allowed and (self._stationary(ctx) or not self.state["reconciled"]):
            if self.state["reconciled"] and self.stop_error is not None:
                self.handle.fail(*self.stop_error)
                self._finish(ctx, "failed")
            elif self.state["reconciled"] and self.completing:
                if self.state["operations"][-1]["untransferredUl"] <= ctx.config.pumpToleranceUl:
                    self._finish(ctx, "succeeded")
                    completed_success = self.state["operations"][-1]["status"] == "succeeded"
                else:
                    self.state["operations"][-1]["fault"] = "quantity_unreachable"
                    self.handle.fail(
                        "quantity_unreachable", "Settled quantity exceeds the fixed remainder budget"
                    )
                    self._finish(ctx, "failed")
            elif self.state["reconciled"]:
                self.handle.cancelled()
                self._finish(ctx, "cancelled")
            else:
                self.handle.fail(
                    self.state["operations"][-1]["fault"],
                    "Actual pump progress cannot be reconciled with requested liquid transfer",
                )
                self._finish(ctx, "failed")
        safe = not self.contact_blocked and self._stationary(ctx)
        if self.handle is None and safe:
            self.stopping, self.hold_targets = False, None
        self._observe_safety(ctx, safe, phase="idle" if completed_success else "holding")

    def _stationary(self, ctx):
        for joint in [*self.axes, self.pump]:
            q, velocity = ctx.physics.joint_q(joint), ctx.physics.joint_qd(joint)
            pump_tolerance = ctx.config.pumpToleranceUl / ctx.config.pumpUlPerUnit
            speed_tolerance = (
                min(ctx.config.holdSpeedTolerance, pump_tolerance)
                if joint == self.pump
                else ctx.config.holdSpeedTolerance
            )
            if (
                not np.isfinite(q).all()
                or not np.isfinite(velocity).all()
                or np.max(np.abs(velocity)) > speed_tolerance
            ):
                return False
            if self.stopping and self.hold_targets:
                tolerance = pump_tolerance if joint == self.pump else ctx.config.axisToleranceM
                if np.max(np.abs(q - self.hold_targets[joint])) > tolerance:
                    return False
        return True

    def start(self, inv, ctx):
        if inv.action in ("requestControl", "confirmControl"):
            # accept committed the request even while paused; start is receipt only.
            result = self.control_receipts[inv.params["operationId"]]
            inv.succeed(result)
            return
        rejection = self.accept(inv, ctx)
        if rejection:
            inv.fail(rejection.code, rejection.message)
            return
        p, action = inv.params, base_action(inv.action)
        self.quantity = None
        if action in ("aspirate", "dispense", "mix"):
            self.quantity = self._quantity_plan(inv, ctx, self._vessel(p["vessel"]))
            if isinstance(self.quantity, Reject):
                inv.fail(self.quantity.code, self.quantity.message)
                self.quantity = None
                return
        operation = {
            "operationId": p["operationId"],
            "action": inv.action,
            "status": "running",
            "appliedUl": 0.0,
            "requestedUl": self.quantity["requestedUl"] if self.quantity else 0.0,
            "vessel": p.get("vessel", ""),
            "epoch": p["epoch"],
            "startedSimTime": ctx.sim_time,
            "observedUl": 0.0,
            "fault": "",
            "invocationId": inv.id,
            "owner": p["owner"],
            "paramsJson": json.dumps(p, sort_keys=True),
            "tipId": self.state["tip"]["id"] or p.get("tip", ""),
            "endedSimTime": -1.0,
            "materialRevision": self.state["revision"] + 1,
            "quantityKind": "none",
            "resolvedUl": 0.0,
            "remainingUl": 0.0,
            "resolutionResidualUl": 0.0,
            "resolutionUl": 0.0,
            "untransferredUl": 0.0,
            "pumpStartM": 0.0,
            "pumpTargetM": 0.0,
            "quantityOutcome": "not_applicable",
            "remainderLocation": "none",
            "noTransfer": False,
        }
        if self.quantity:
            operation.update(self.quantity)
            operation["noTransfer"] = operation["resolvedUl"] == 0
            multiplier = 2 if action == "mix" else 1
            operation.update(
                remainingUl=operation["resolvedUl"] * multiplier,
                untransferredUl=operation["requestedUl"] * multiplier,
            )
        self.state["operations"].append(operation)
        self.state["operationCount"] += 1
        self.used_operations.add(p["operationId"])
        self.handle, self.started = inv, ctx.sim_time
        self.stopping = False
        self.stop_error = None
        self.completing = False
        self.quantity_previous_q, self.quantity_stable_samples = None, 0
        self.quantity_previous_frame = None
        self.cancelled_at_tick = None
        ctx.set(
            controlSafe=False,
            phase=inv.action,
            lastOperationId=p["operationId"],
            lastAppliedUl=0.0,
            remainingUl=operation["remainingUl"],
            lastResolvedUl=operation["resolvedUl"],
            lastResolutionResidualUl=operation["resolutionResidualUl"],
            lastUntransferredUl=operation["untransferredUl"],
            lastQuantityOutcome=operation["quantityOutcome"],
        )
        if action == "move":
            origin = np.array([float(ctx.physics.joint_q(joint)[0]) for joint in self.axes])
            goal = np.array(p["position"], dtype=float)
            distance = np.abs(goal - origin)
            speed = np.array([ctx.config.xySpeedMPerS] * 2 + [ctx.config.zSpeedMPerS])
            # Quintic smoothstep peaks: |s'|=15/8, |s''|=10/sqrt(3).
            # One shared duration synchronizes XYZ and keeps each axis bounded.
            duration = max(
                float(np.max(1.875 * distance / speed)),
                float(np.sqrt(np.max((10 / np.sqrt(3)) * distance / ctx.config.axisAccelerationMPerS2))),
            )
            self.motion = (origin, goal, duration)
            self._move_target(ctx)
        elif action in ("aspirate", "dispense", "mix"):
            if self.quantity["resolvedUl"] == 0:
                self._mark_tip_contact(self._vessel(p["vessel"]))
                self._finish(ctx, "succeeded")
                return
            origin = float(ctx.physics.joint_q(self.pump)[0])
            direction = -1 if action == "dispense" else 1
            self.stroke = {
                "origin": origin,
                "appliedUl": 0.0,
                "completedUl": 0.0,
                "direction": direction,
                "volumeUl": self.quantity["resolvedUl"],
            }
            ctx.physics.set_joint_target(self.pump, [self.quantity["pumpTargetM"]])
        else:
            self._immediate(ctx)
        if self.handle:
            self._publish(ctx)

    def _immediate(self, ctx):
        inv, tip = self.handle, self.state["tip"]
        p, action = inv.params, inv.action
        if action == "reserve":
            self.state["reservations"] = [
                r for r in self.state["reservations"] if r["resource"] != p["resource"]
            ]
            self.state["reservations"].append({k: p[k] for k in ("resource", "device", "owner", "epoch")})
        elif action == "release":
            self.state["reservations"] = [
                r for r in self.state["reservations"] if r["resource"] != p["resource"]
            ]
        elif action == "pickupTip":
            ctx.physics.weld(self.pipette_body, self.tip_bodies[p["tip"]])
            next(r for r in self.state["tips"] if r["id"] == p["tip"])["state"] = "installed"
            tip["id"] = p["tip"]
        elif action == "discardTip":
            move_content(tip, self._vessel(p["waste"]), tip["volumeUl"])
            next(r for r in self.state["tips"] if r["id"] == tip["id"])["state"] = "discarded"
            ctx.physics.unweld(self.pipette_body, self.tip_bodies[tip["id"]])
            self.state["tip"] = empty_content() | {"id": "", "contactCulture": "", "contactWaste": False}
            ctx.physics.set_joint_target(self.pump, [0.0])
        elif action == "detach":
            for cell in self._vessel(p["vessel"])["cells"]:
                for viability in ("Live", "Dead"):
                    cell["suspended" + viability] += cell["attached" + viability]
                    cell["attached" + viability] = 0.0
        elif action in ("observe", "count"):
            row = self._vessel(p["vessel"])
            live = sum(c["suspendedLive"] + c["attachedLive"] + c["pelletLive"] for c in row["cells"])
            dead = sum(c["suspendedDead"] + c["attachedDead"] + c["pelletDead"] for c in row["cells"])
            self.state["observations"].append(
                {
                    "operationId": p["operationId"],
                    "vessel": row["id"],
                    "materialRevision": self.state["revision"],
                    "source": "simulated-state-model",
                    "live": live,
                    "dead": dead,
                    "volumeUl": row["volumeUl"],
                    "simTime": ctx.sim_time,
                    "kind": action,
                    "entityKey": row["entityKey"],
                    "culture": row["culture"],
                    "parent": row["parent"],
                    "modelLive": live,
                    "modelDead": dead,
                    "measuredLive": live,
                    "measuredDead": dead,
                }
            )
            ctx.set(
                observationRevision=len(self.state["observations"]),
                lastObservedLive=live,
                lastObservedDead=dead,
            )
        elif action == "pellet":
            for name in p["vessels"]:
                row = self._vessel(name)
                for cell in row["cells"]:
                    for viability in ("Live", "Dead"):
                        cell["pellet" + viability] += cell["suspended" + viability]
                        cell["suspended" + viability] = 0.0
                row["processedCycle"], row["mixed"] = p["cycle"], False
        elif action == "wait":
            return
        self._finish(ctx, "succeeded")

    def _mark_tip_contact(self, row):
        tip = self.state["tip"]
        if row["waste"]:
            tip["contactWaste"] = True
        else:
            origins = content_origins(row) | content_origins(tip)
            tip["contactCulture"] = (
                row["parent"] or row["culture"] or next(iter(origins), tip["contactCulture"])
            )

    def _apply_stroke(self, ctx):
        if not self.handle or not self.stroke:
            return
        inv, stroke = self.handle, self.stroke
        p, action = inv.params, base_action(inv.action)
        requested = stroke["volumeUl"]
        feedback_tolerance = (
            ctx.config.pumpToleranceUl
            if self.quantity["quantityKind"] == "literal"
            else self.quantity["resolutionUl"]
        )
        actual = (
            (float(ctx.physics.joint_q(self.pump)[0]) - stroke["origin"])
            * stroke["direction"]
            * ctx.config.pumpUlPerUnit
        )
        operation = self.state["operations"][-1]
        operation["observedUl"] = actual if np.isfinite(actual) else -1.0
        if (
            not np.isfinite(actual)
            or actual > requested + feedback_tolerance
            or (not self.stopping and actual < stroke["appliedUl"] - feedback_tolerance)
            or actual < -feedback_tolerance
        ):
            self.state["reconciled"] = False
            operation["fault"] = "pump_feedback_invalid"
            self._publish(ctx)
            return
        actual = max(0.0, actual)
        change = actual - stroke["appliedUl"]
        amount = abs(change)
        if amount <= 1e-10:
            return
        if self._accessible(ctx, p["vessel"]):
            self.state["reconciled"] = False
            operation["fault"] = "transfer_conditions_changed"
            self._publish(ctx)
            return
        row, tip = self._vessel(p["vessel"]), self.state["tip"]
        aspirating = action == "aspirate" or (action == "mix" and stroke["direction"] == 1)
        source, destination = (row, tip) if aspirating else (tip, row)
        if change < 0:
            source, destination = destination, source
        capacity = destination.get("capacityUl", ctx.config.tipCapacityUl)
        if amount > source["volumeUl"] + 1e-8 or destination["volumeUl"] + amount > capacity + 1e-8:
            self.state["reconciled"] = False
            operation["fault"] = "pump_feedback_invalid"
            self._publish(ctx)
            return
        move_content(source, destination, amount)
        self._mark_tip_contact(row)
        if not aspirating and any(c["name"] == "trypsin" and c["volumeUl"] > 0 for c in row["components"]):
            if row["exposureSince"] < 0:
                row["exposureSince"] = ctx.sim_time
        stroke["appliedUl"] = actual
        total = actual + stroke["completedUl"]
        requested_total = requested * (2 if action == "mix" else 1)
        self.state["operations"][-1]["appliedUl"] = total
        remaining = max(0, requested_total - total)
        untransferred = max(0, self.quantity["requestedUl"] * (2 if action == "mix" else 1) - total)
        operation.update(remainingUl=remaining, untransferredUl=untransferred)
        ctx.set(lastAppliedUl=total, remainingUl=remaining, lastUntransferredUl=untransferred)
        self._publish(ctx)

    def _compact_operations(self):
        def protected(row):
            return (
                row["status"] in ("running", "settling")
                or bool(row["fault"])
                or (row["status"] != "succeeded" and row["appliedUl"] > 0)
            )

        ordinary = [row["operationId"] for row in self.state["operations"] if not protected(row)]
        keep = set(ordinary[-RECENT_OPERATIONS:])
        previous = self.state["operations"]
        self.state["operations"] = [row for row in previous if protected(row) or row["operationId"] in keep]
        self.state["archivedOperationCount"] += len(previous) - len(self.state["operations"])

    def _finish(self, ctx, status):
        inv = self.handle
        if inv.invocation.status in ("failed", "cancelled"):
            status = inv.invocation.status
        operation = self.state["operations"][-1]
        operation.update(
            status=status, endedSimTime=ctx.sim_time, materialRevision=self.state["revision"] + 1
        )
        if status == "failed" and not operation["fault"] and inv.invocation.error:
            operation["fault"] = inv.invocation.error.code
        if operation["appliedUl"] > 0:
            operation["quantityOutcome"] = (
                "transferred_with_remainder" if operation["untransferredUl"] else "transferred"
            )
            if status != "succeeded":
                operation["quantityOutcome"] = "partial"
        elif operation["quantityKind"] != "none" and status != "succeeded":
            operation["quantityOutcome"] = "not_completed"
        receipt = dict(operation)
        self.state["terminalOperationCount"] += 1
        self._compact_operations()
        ctx.emit("operationCompleted", receipt=receipt)
        if status == "succeeded":
            inv.succeed(
                {
                    "operationId": inv.params["operationId"],
                    "materialRevision": self.state["revision"] + 1,
                    "receipt": receipt,
                }
            )
        self.handle, self.stroke, self.motion = None, None, None
        self.stopping = False
        self.stop_error = None
        self.quantity = None
        self.completing = False
        self.cancelled_at_tick = None
        self._observe_safety(
            ctx, not self.contact_blocked and self._stationary(ctx),
            lastQuantityOutcome=operation["quantityOutcome"],
            phase="idle",
        )
        self._publish(ctx)

    def _move_target(self, ctx):
        origin, goal, duration = self.motion
        elapsed = max(0.0, ctx.sim_time - self.started)
        u = min(elapsed / duration, 1.0) if duration else 1.0
        fraction = u**3 * (10 + u * (-15 + 6 * u))
        velocity = (30 * u**2 * (1 - u)**2 / duration) if duration else 0.0
        target = goal if u == 1 else origin + (goal - origin) * fraction
        for joint, position, speed in zip(self.axes, target, (goal - origin) * velocity, strict=True):
            ctx.physics.set_joint_target(joint, [float(position)])
            ctx.physics.set_joint_target_velocity(joint, [float(speed)])
        return u == 1

    def tick(self, ctx, dt):
        for joint in [*self.axes, self.pump]:
            measured = ctx.physics.joint_q(joint)
            if np.isfinite(measured).all():
                self.last_good_q[joint] = measured.copy()
        inv = self.handle
        contact_failure = self._contact_failure(ctx)
        if contact_failure:
            self.completing = False
            self._hold(ctx, (contact_failure.code, contact_failure.message) if inv else None)
            return
        if inv is None:
            if self.stopping:
                self._hold(ctx)
            else:
                self._observe_safety(ctx, not self.contact_blocked and self._stationary(ctx))
            return
        if self.stopping and not self.completing:
            self._hold(ctx)
            return
        rejection = guard(ctx, ctx.key, inv.params)
        if rejection:
            self.completing = False
            self._hold(ctx)
            return
        p, action = inv.params, base_action(inv.action)
        if action in LIQUID_ACTIONS and not self._cabinet_ready(ctx):
            self._hold(ctx, ("cabinet_not_ready", "Cabinet readiness was lost during liquid handling"))
            return
        if inv.action == "aspirateTopUp":
            failed = self._top_up_target(inv, ctx, self._vessel(p["vessel"]))
            if isinstance(failed, Reject):
                self._hold(ctx, (failed.code, failed.message))
                return
        if self.stopping:
            self._hold(ctx)
            return
        if action in ("aspirate", "dispense", "mix"):
            rejection = self._accessible(ctx, p["vessel"])
            if rejection:
                self._hold(ctx)
                return
            q = float(ctx.physics.joint_q(self.pump)[0])
            self._apply_stroke(ctx)
            if not self.state["reconciled"]:
                self._hold(ctx)
                return
            complete = self.stroke["volumeUl"] - self.stroke["appliedUl"] <= ctx.config.pumpToleranceUl
            if self.quantity["quantityKind"] != "literal":
                frame = getattr(ctx.physics, "last_time", ctx.sim_time)
                if frame is not None and (
                    self.quantity_previous_frame is None or frame > self.quantity_previous_frame
                ):
                    self.quantity_stable_samples = (
                        self.quantity_stable_samples + 1 if q == self.quantity_previous_q else 1
                    )
                    self.quantity_previous_q, self.quantity_previous_frame = q, frame
                if self.stroke["appliedUl"] > 0 and self.quantity_stable_samples >= 3:
                    if self.state["operations"][-1]["untransferredUl"] <= ctx.config.pumpToleranceUl:
                        # Resolved position can plateau while the backend still
                        # reports a tiny velocity. Establish an actual holding
                        # target; do not reinterpret that velocity as standstill.
                        self.completing = True
                        self._hold(ctx)
                    else:
                        self._hold(
                            ctx,
                            (
                                "quantity_unreachable",
                                "Pump position settled before the fixed quantity tolerance was met",
                            ),
                        )
                    return
                complete = False
            if complete and self._stationary(ctx):
                if action == "mix" and self.stroke["direction"] == 1:
                    origin = self.stroke["origin"]
                    self.stroke.update(
                        origin=q, direction=-1, completedUl=self.stroke["appliedUl"], appliedUl=0.0
                    )
                    ctx.physics.set_joint_target(self.pump, [origin])
                    return
                if action == "mix":
                    row = self._vessel(p["vessel"])
                    for cell in row["cells"]:
                        for viability in ("Live", "Dead"):
                            cell["suspended" + viability] += cell["pellet" + viability]
                            cell["pellet" + viability] = 0.0
                    row["mixed"] = True
                self._finish(ctx, "succeeded")
            elif self.handle:
                inv.progress(
                    self.state["operations"][-1]["appliedUl"]
                    / (self.stroke["volumeUl"] * (2 if action == "mix" else 1))
                )
        elif action == "wait":
            if ctx.sim_time - self.started >= p["seconds"]:
                self._finish(ctx, "succeeded")
        elif action == "move":
            if self._move_target(ctx) and self._stationary(ctx) and all(
                abs(float(ctx.physics.joint_q(j)[0]) - value) <= ctx.config.axisToleranceM
                for j, value in zip(self.axes, p["position"], strict=True)
            ):
                self._finish(ctx, "succeeded")

    def cancel(self, inv, ctx):
        if self.handle and self.handle.id == inv.id:
            self.completing = False
            # Core may assign a timeout failure immediately after this callback.
            # Finalize the accounting receipt on the next tick, preserving that
            # authoritative status instead of emitting a premature cancellation.
            self.cancelled_at_tick = ctx.tick_index
            self._hold(ctx)
        else:
            inv.cancelled()
