"""Flex actuator owner and atomic, recorded material/control state.

Liquid is a symbolic model driven by measured pump displacement, never elapsed
time or the requested target. PhysicsPort supplies the mechanical observations.
"""

import json

import numpy as np
from pydantic import ConfigDict, Field, model_validator

from packages.info.program import ProgramConfig, Reject

from .guard import guard


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
        axisJoints: list[str] = Field(
            min_length=3, max_length=3, json_schema_extra={"spatialReference": "joint"}
        )
        pumpJoint: str = Field(json_schema_extra={"spatialReference": "joint"})
        tipSite: str = Field(json_schema_extra={"spatialReference": "site"})
        pipetteBody: str = Field(json_schema_extra={"spatialReference": "body"})
        vessels: list[Vessel]
        tips: list[Tip]
        managedDevices: list[str] = []
        pumpUlPerUnit: float = Field(default=100000, gt=0)
        tipCapacityUl: float = Field(default=1000, gt=0)
        alignmentToleranceM: float = Field(default=0.003, gt=0)
        axisToleranceM: float = Field(default=0.0005, gt=0)
        holdSpeedTolerance: float = Field(default=0.0001, gt=0)
        pumpToleranceUl: float = Field(default=0.01, gt=0)
        wasteSite: str = ""
        detachSeconds: float = Field(default=2, gt=0)
        imagerStation: str = ""
        centrifugeSeconds: float = Field(default=2, gt=0)

        @model_validator(mode="after")
        def unique_compartments(self):
            for rows in (self.vessels, self.tips):
                if len({row.id for row in rows}) != len(rows):
                    raise ValueError("Compartment and tip identities must be unique")
            return self

    def init(self, ctx):
        self.axes = [ctx.physics.bind_joint(key) for key in ctx.config.axisJoints]
        self.pump = ctx.physics.bind_joint(ctx.config.pumpJoint)
        self.last_good_q = {joint: ctx.physics.joint_q(joint).copy() for joint in [*self.axes, self.pump]}
        self.tip_site = ctx.physics.bind_site(ctx.config.tipSite)
        self.pipette_body = ctx.physics.bind_body(ctx.config.pipetteBody)
        self.tip_bodies = {row.id: ctx.physics.bind_body(row.bodyKey) for row in ctx.config.tips}
        self.configs = {row.id: row for row in ctx.config.vessels}
        self.sites = {row.id: ctx.physics.bind_site(row.site) for row in ctx.config.vessels}
        self.tip_sites = {row.id: ctx.physics.bind_site(row.site) for row in ctx.config.tips}
        self.handle, self.stroke, self.pending = None, None, None
        self.stopping = False
        self.hold_targets = None
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
            "tip": empty_content() | {"id": "", "contactCulture": ""},
            "reservations": [],
            "operations": [],
            "observations": [],
            "controlRequests": [],
        }
        self.initial_volume = sum(row["volumeUl"] for row in vessels)
        self.initial_cells = self._cell_totals()
        ctx.set(
            controlOwner="auto",
            controlEpoch=0,
            controlMode="auto",
            controlSafe=True,
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
        )

    def _publish(self, ctx):
        self.state["revision"] += 1
        total = sum(row["volumeUl"] for row in self.state["vessels"]) + self.state["tip"]["volumeUl"]
        cell_totals = self._cell_totals()
        balanced = (
            self.state["reconciled"]
            and abs(total - self.initial_volume) < 1e-6
            and all(
                abs(value - cell_totals.get(culture, 0)) < 1e-5
                for culture, value in self.initial_cells.items()
            )
        )
        independent = all(
            len(
                {
                    cell["culture"]
                    for cell in row["cells"]
                    if sum(value for key, value in cell.items() if key not in ("culture", "parent")) > 1e-8
                }
            )
            <= 1
            for row in self.state["vessels"]
            if not row["waste"]
        )
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

    def _aligned(self, ctx, site):
        return (
            np.linalg.norm(
                np.array(ctx.physics.site_pose(self.tip_site)[0]) - np.array(ctx.physics.site_pose(site)[0])
            )
            <= ctx.config.alignmentToleranceM
        )

    def _accessible(self, ctx, name):
        config = self.configs[name]
        if config.lidProperty and ctx.read(config.entityKey, config.lidProperty) is not False:
            return Reject(code="closed_vessel", message="Vessel lid must be confirmed open")
        if config.requiredStation and (
            ctx.read(config.entityKey, "station") != config.requiredStation
            or ctx.read(config.entityKey, "settled") is not True
        ):
            return Reject(code="wrong_station", message="Vessel is not settled at its operation station")
        if not self._aligned(ctx, self.sites[name]):
            return Reject(code="not_aligned", message="Actual pipette tip is not aligned with the vessel")

    def _reject(self, code, message):
        return Reject(code=code, message=message)

    def accept(self, inv, ctx):
        p, action = inv.params, inv.action
        if any(row["operationId"] == p["operationId"] for row in self.state["operations"]):
            return self._reject(
                "operation_applied", "This domain operation has already been applied; use current remainder"
            )
        if action in ("requestControl", "confirmControl"):
            return self._control(inv, ctx)
        if any(row["operationId"] == p["operationId"] for row in self.state["controlRequests"]):
            return self._reject(
                "operation_applied", "This domain identity belongs to an applied control request"
            )
        rejection = guard(ctx, ctx.key, p)
        if rejection:
            return rejection
        if not self.state["reconciled"]:
            return self._reject(
                "material_unreconciled",
                "Actuator feedback invalidated the material estimate; preserve this interrupted run",
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
            if self.handle or self.state["tip"]["volumeUl"] > 1e-8:
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
        if action == "discardTip":
            if not self.state["tip"]["id"]:
                return self._reject("tip_unavailable", "No tip is installed")
            if not ctx.config.wasteSite or not self._aligned(
                ctx, ctx.physics.bind_site(ctx.config.wasteSite)
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
                tip, amount = self.state["tip"], p["volumeUl"]
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
                cultures = {cell["culture"] for cell in row["cells"]}
                contact = tip["contactCulture"]
                allowed = {row["culture"], row["parent"]} | cultures
                if contact and not row["waste"] and contact not in allowed:
                    return self._reject(
                        "cross_sample", "Sample-contact tips cannot enter another culture or common reagent"
                    )
                if (
                    action == "dispense"
                    and cultures
                    and contact
                    and contact not in cultures
                    and contact != row["parent"]
                ):
                    return self._reject(
                        "cross_sample", "Destination culture is not descended from this sample"
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
        return None

    def _control(self, inv, ctx):
        p = inv.params
        old = next((r for r in self.state["controlRequests"] if r["operationId"] == p["operationId"]), None)
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
            if (
                ctx.get("controlMode") != "holding"
                or ctx.get("controlSafe") is not True
                or not self._stationary(ctx)
            ):
                return self._reject("not_safe", "Workcell has not established safe hold")
            if any(ctx.read(key, "controlSafe") is not True for key in ctx.config.managedDevices):
                return self._reject("not_safe", "A managed device has not established safe hold")
        elif ctx.get("controlMode") == "holding":
            return self._reject("handoff_pending", "Confirm the current handoff before requesting another")
        if inv.action == "requestControl":
            ctx.set(controlEpoch=ctx.get("controlEpoch") + 1, controlMode="holding", controlSafe=False)
            self.pending = {"owner": p["nextOwner"], "mode": p["mode"]}
            self._hold(ctx)
        else:
            ctx.set(controlOwner=self.pending["owner"], controlMode=self.pending["mode"])
            self.state["reservations"] = [
                r | {"owner": self.pending["owner"], "epoch": ctx.get("controlEpoch")}
                for r in self.state["reservations"]
            ]
            self.pending = None
        self.state["controlRequests"].append(
            {
                "operationId": p["operationId"],
                "epoch": ctx.get("controlEpoch"),
                "mode": ctx.get("controlMode"),
                "owner": ctx.get("controlOwner"),
                "action": inv.action,
                "paramsJson": json.dumps(p, sort_keys=True),
            }
        )
        self._publish(ctx)
        return None

    def _hold(self, ctx):
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
        if self.handle and (self._stationary(ctx) or not self.state["reconciled"]):
            if self.state["reconciled"]:
                self.handle.cancelled()
                self._finish(ctx, "cancelled")
            else:
                self.handle.fail(
                    self.state["operations"][-1]["fault"],
                    "Actual pump progress cannot be reconciled with requested liquid transfer",
                )
                self._finish(ctx, "failed")
        ctx.set(controlSafe=self._stationary(ctx), phase="holding")

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
            result = next(
                r for r in self.state["controlRequests"] if r["operationId"] == inv.params["operationId"]
            )
            inv.succeed(result)
            return
        rejection = self.accept(inv, ctx)
        if rejection:
            inv.fail(rejection.code, rejection.message)
            return
        p, action = inv.params, inv.action
        operation = {
            "operationId": p["operationId"],
            "action": action,
            "status": "running",
            "appliedUl": 0.0,
            "requestedUl": p.get("volumeUl", 0.0),
            "vessel": p.get("vessel", ""),
            "epoch": p["epoch"],
            "startedSimTime": ctx.sim_time,
            "observedUl": 0.0,
            "fault": "",
        }
        self.state["operations"].append(operation)
        self.handle, self.started = inv, ctx.sim_time
        self.stopping = False
        ctx.set(
            controlSafe=False,
            phase=action,
            lastOperationId=p["operationId"],
            lastAppliedUl=0.0,
            remainingUl=p.get("volumeUl", 0.0),
        )
        if action == "move":
            for joint, value in zip(self.axes, p["position"], strict=True):
                ctx.physics.set_joint_target(joint, [value])
        elif action in ("aspirate", "dispense", "mix"):
            origin = float(ctx.physics.joint_q(self.pump)[0])
            direction = -1 if action == "dispense" else 1
            self.stroke = {
                "stage": "transfer",
                "origin": origin,
                "appliedUl": 0.0,
                "completedUl": 0.0,
                "direction": direction,
            }
            ctx.physics.set_joint_target(
                self.pump, [origin + direction * p["volumeUl"] / ctx.config.pumpUlPerUnit]
            )
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
            self.state["tip"] = empty_content() | {"id": "", "contactCulture": ""}
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

    def _apply_stroke(self, ctx):
        if not self.handle or not self.stroke:
            return
        inv, stroke = self.handle, self.stroke
        p, action = inv.params, inv.action
        requested = p["volumeUl"]
        actual = (
            (float(ctx.physics.joint_q(self.pump)[0]) - stroke["origin"])
            * stroke["direction"]
            * ctx.config.pumpUlPerUnit
        )
        operation = self.state["operations"][-1]
        operation["observedUl"] = actual if np.isfinite(actual) else -1.0
        if (
            not np.isfinite(actual)
            or actual > requested + ctx.config.pumpToleranceUl
            or (not self.stopping and actual < stroke["appliedUl"] - ctx.config.pumpToleranceUl)
            or actual < -ctx.config.pumpToleranceUl
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
        if not row["waste"] and (row["parent"] or row["culture"]):
            tip["contactCulture"] = row["parent"] or row["culture"]
        if not aspirating and any(c["name"] == "trypsin" and c["volumeUl"] > 0 for c in row["components"]):
            if row["exposureSince"] < 0:
                row["exposureSince"] = ctx.sim_time
        stroke["appliedUl"] = actual
        total = actual + stroke["completedUl"]
        requested_total = requested * (2 if action == "mix" else 1)
        self.state["operations"][-1]["appliedUl"] = total
        ctx.set(lastAppliedUl=total, remainingUl=max(0, requested_total - total))
        self._publish(ctx)

    def _finish(self, ctx, status):
        inv = self.handle
        self.state["operations"][-1]["status"] = status
        if status == "succeeded":
            inv.succeed(
                {"operationId": inv.params["operationId"], "materialRevision": self.state["revision"] + 1}
            )
        self.handle, self.stroke = None, None
        self.stopping = False
        ctx.set(controlSafe=self._stationary(ctx), phase="idle")
        self._publish(ctx)

    def tick(self, ctx, dt):
        for joint in [*self.axes, self.pump]:
            measured = ctx.physics.joint_q(joint)
            if np.isfinite(measured).all():
                self.last_good_q[joint] = measured.copy()
        inv = self.handle
        if inv is None:
            ctx.set(controlSafe=self._stationary(ctx))
            return
        if self.stopping:
            self._hold(ctx)
            return
        rejection = guard(ctx, ctx.key, inv.params)
        if rejection:
            self._hold(ctx)
            return
        p, action = inv.params, inv.action
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
            if p["volumeUl"] - self.stroke["appliedUl"] <= ctx.config.pumpToleranceUl and self._stationary(
                ctx
            ):
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
                    / (p["volumeUl"] * (2 if action == "mix" else 1))
                )
        elif action == "wait":
            if ctx.sim_time - self.started >= p["seconds"]:
                self._finish(ctx, "succeeded")
        elif action == "move":
            if self._stationary(ctx) and all(
                abs(float(ctx.physics.joint_q(j)[0]) - value) <= ctx.config.axisToleranceM
                for j, value in zip(self.axes, p["position"], strict=True)
            ):
                self._finish(ctx, "succeeded")

    def cancel(self, inv, ctx):
        if self.handle and self.handle.id == inv.id:
            self._hold(ctx)
        else:
            inv.cancelled()
