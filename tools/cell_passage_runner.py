"""Cell Passage client scheduler. Device actions remain in the public HTTP pipeline.

This module deliberately does not change the ordinary ``lw plan run`` semantics.
Call ``advance`` repeatedly; paused and observing results are not action failures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import uuid
from pathlib import Path

from apps.cli.lw.client import TERMINAL, GatewayError


class PassageExecutor:
    def __init__(self, client, plan, workcell, owner, epoch, material_revision):
        self.client, self.plan_id, self.revision = client, plan["id"], plan["revision"]
        self.workcell, self.owner, self.control_epoch = workcell, owner, epoch
        self.target = {name: plan[name] for name in ("sessionId", "sceneRevision", "epoch")}
        self.active = None
        self.blocked = None
        self.material_revision = material_revision
        self.settling_since = None
        self.managed_devices = None

    def _settling(self, sim_time, reason, **details):
        if self.settling_since is None:
            self.settling_since = sim_time
        elapsed = sim_time - self.settling_since
        return {"status": "budget_exceeded" if elapsed > self.budget_seconds else "observing",
                "reason": reason, "elapsedSimSeconds": elapsed, **details}

    @classmethod
    async def open(cls, client, plan_id, workcell, *, owner, epoch, budget_seconds=120):
        if not math.isfinite(budget_seconds) or budget_seconds <= 0:
            raise ValueError("budget_seconds must be finite and positive")
        plan = await client.plan_show(plan_id)
        if (plan.get("kind") != "operation" or plan.get("target") != "session"
                or plan["status"] != "approved" or plan["revision"] != 1
                or any(step["status"] != "pending" for step in plan["steps"])):
            raise ValueError("A new executor requires a new round; reconcile only in the existing process")
        values = (await client.get_state([workcell]))["entities"][workcell]["values"]
        executor = cls(client, plan, workcell, owner, epoch, values["materialRevision"])
        executor.budget_seconds = budget_seconds
        return executor

    async def inspect(self):
        """Return the public checkpoint to inspect before explicit same-process reconciliation."""
        session = await self.client.status()
        plan = await self.client.plan_show(self.plan_id)
        values = (await self.client.get_state([self.workcell]))["entities"][self.workcell]["values"]
        return {
            **{name: session[name] for name in self.target},
            "planRevision": plan["revision"],
            **{name: values[name] for name in (
                "controlOwner", "controlEpoch", "controlMode", "controlSafe", "materialRevision",
            )},
        }

    async def reconcile(self, expected, *, checks):
        """Adopt a reviewed checkpoint after revision, public mechanical checks and quiescence.

        The scene-specific caller supplies checks for actual holds, doors, rotor,
        occupancy and locations. This does not rewrite failed results or material.
        """
        if not checks:
            raise ValueError("Reconciliation requires explicit public mechanical checks")
        if await self.inspect() != expected:
            raise ValueError("Checkpoint changed; inspect again")
        if any(expected[name] != value for name, value in self.target.items()):
            raise ValueError("Session reset or replaced; start a new round")
        if expected["controlMode"] != "auto" or not expected["controlSafe"]:
            raise ValueError("Control must be auto and devices safely stopped")
        if self.active and self.active.get("invocationId"):
            invocation = await self.client.get_invocation(self.active["invocationId"], timeout=1)
            if invocation["status"] not in TERMINAL:
                raise ValueError("Observe or cancel the in-flight invocation before reconciliation")
        elif self.active:
            raise ValueError("Admission remains unknown; retain this interrupted round")
        plan = await self.client.plan_show(self.plan_id)
        if (plan["status"] not in ("approved", "running", "completed")
                or any(step["status"] not in ("pending", "done", "skipped") for step in plan["steps"])):
            raise ValueError("Use the revision API to rebuild failed steps as pending")
        values = (await self.client.get_state([self.workcell]))["entities"][self.workcell]["values"]
        if not values["auditPassed"] or not values["materialSnapshot"]["reconciled"]:
            raise ValueError("Material audit failed; retain the interrupted round")
        for condition in checks:
            op = next(key for key in condition if key not in ("entity", "property", "message", "by"))
            result = await self.client.check_condition(
                condition["entity"], condition["property"], op, condition[op], by=condition.get("by")
            )
            if not result["satisfied"]:
                raise ValueError("Mechanical reconciliation check failed")
        if await self.inspect() != expected:
            raise ValueError("Checkpoint changed during reconciliation; inspect again")
        self.revision = expected["planRevision"]
        self.owner, self.control_epoch = expected["controlOwner"], expected["controlEpoch"]
        self.material_revision = expected["materialRevision"]
        self.active, self.blocked = None, None
        self.settling_since = None
        return {"status": "ready", "checkpoint": expected}

    async def advance(self):
        """Observe once and dispatch at most one step, never replaying an uncertain POST."""
        try:
            return await self._advance()
        except GatewayError as error:
            if error.status_code is not None:
                raise
            return {"status": "observing", "reason": "network_unavailable", "active": self.active,
                    "dispatchStopped": bool(self.blocked)}

    async def _advance(self):
        plan = await self.client.plan_show(self.plan_id)
        session = await self.client.status()
        values = (await self.client.get_state([self.workcell]))["entities"][self.workcell]["values"]
        if any(session[name] != expected for name, expected in self.target.items()):
            self.blocked = "session_changed"
        if plan["revision"] != self.revision:
            self.blocked = "plan_revised"
        if plan.get("persistence", {}).get("status") == "failed":
            self.blocked = "plan_persistence_failed"
        if (values["controlOwner"] != self.owner or values["controlEpoch"] != self.control_epoch
                or values["controlMode"] != "auto"):
            self.blocked = "control_changed"
        if not self.active and values["materialRevision"] != self.material_revision:
            self.blocked = self.blocked or "material_changed"

        # Even after losing control, continue observing the already accepted call.
        invocation = None
        if self.active:
            step_id = self.active["stepId"]
            invocation_id = plan.get("stepResults", {}).get(str(step_id), {}).get("invocationId")
            invocation_id = invocation_id or self.active.get("invocationId")
            if invocation_id:
                self.active["invocationId"] = invocation_id
                try:
                    invocation = await self.client.get_invocation(invocation_id, timeout=1)
                except GatewayError as error:
                    if error.status_code is not None:
                        raise
                    return {"status": "observing", "stepId": step_id, "invocationId": invocation_id,
                            "dispatchStopped": bool(self.blocked), "reason": "network_unavailable"}
                if invocation["status"] in TERMINAL:
                    # The Plan is authoritative for expect checks as well as invocation success.
                    plan = await self.client.plan_show(self.plan_id)
                    step = next((row for row in plan["steps"] if row["id"] == step_id), None)
                    if step is None:
                        self.blocked = "plan_revised"
                    elif invocation["status"] != "succeeded" or step["status"] == "failed":
                        return {"status": "failed", "stepId": step_id, "invocation": invocation}
                    elif step["status"] == "done":
                        values = (await self.client.get_state([self.workcell]))["entities"][
                            self.workcell
                        ]["values"]
                        self.material_revision = values["materialRevision"]
                        self.active = None
            elif self.active.get("uncertain"):
                self.blocked = "admission_unknown"
        if self.blocked:
            return {"status": "reconcile_required", "reason": self.blocked, "invocation": invocation,
                    "materialRevision": values["materialRevision"]}
        if session["state"] == "PAUSED":
            return {"status": "paused", "invocation": invocation,
                    "materialRevision": values["materialRevision"]}
        if session["state"] != "RUNNING":
            return {"status": "interrupted", "reason": session["state"]}
        if self.active:
            if session["simTime"] - self.active["startedSimTime"] > self.budget_seconds:
                return {"status": "budget_exceeded", "invocation": invocation,
                        "elapsedSimSeconds": session["simTime"] - self.active["startedSimTime"]}
            return {"status": "observing", "invocation": invocation}
        if plan["status"] == "completed":
            return {"status": "completed", "plan": plan}
        if not values["controlSafe"]:
            return self._settling(session["simTime"], "devices_settling")
        step = next((row for row in plan["steps"] if row["status"] not in ("done", "skipped")), None)
        if step is None or step["status"] != "pending":
            return {"status": "reconcile_required", "reason": "step_not_pending"}
        if "wait" in step:
            raise ValueError("Use a workcell simTime wait action for pause-aware experimental waits")
        context = {"actor": "cli:cell-passage", "planId": self.plan_id,
                   "stepId": step["id"], "planRevision": self.revision}
        if "check" in step:
            self.settling_since = None
            condition = step["check"]
            op = next(key for key in condition if key not in ("entity", "property", "message", "by"))
            result = await self.client.check_condition(
                condition["entity"], condition["property"], op, condition[op],
                by=condition.get("by"), **context,
            )
            return {"status": "ready" if result["satisfied"] else "failed", "result": result}
        action = step["do"]
        params = action.get("params", {})
        if (params.get("owner") != self.owner or params.get("epoch") != self.control_epoch
                or not params.get("operationId")):
            raise ValueError("Controlled actions require the pinned owner/epoch and an operationId")
        if action["entity"] == self.workcell and action["action"] == "release":
            if self.managed_devices is None:
                catalog = await self.client.catalog()
                self.managed_devices = catalog["entities"][self.workcell]["config"]["managedDevices"]
            if self.managed_devices:
                state = (await self.client.get_state(self.managed_devices))["entities"]
                unsafe = [key for key in self.managed_devices
                          if state.get(key, {}).get("values", {}).get("controlSafe") is not True]
                if unsafe:
                    return self._settling(
                        session["simTime"], "managed_devices_settling", unsafeDevices=unsafe,
                    )
        self.settling_since = None
        self.active = {"stepId": step["id"], "requestId": uuid.uuid4().hex,
                       "startedSimTime": session["simTime"]}
        try:
            result = await self.client.request(
                "POST", await self.client.route() + "/actions", actor=context.pop("actor"),
                json={"requestId": self.active["requestId"], "entityKey": action["entity"],
                      "action": action["action"], "params": params, "wait": False, **context},
            )
        except GatewayError as error:
            # A transport timeout can happen after admission. Never send a second POST.
            if error.status_code is not None:
                self.active = None
                raise
            self.active["uncertain"] = True
            return {"status": "observing", "reason": "admission_unconfirmed"}
        self.active["invocationId"] = result["invocationId"]
        return {"status": "observing", "invocation": result}


async def drive_executor(executor, receipt_path, *, interval=0.1):
    """Drive a fresh client and retain an append-only receipt on normal/exceptional exit.

    The gateway recording remains authoritative. A receipt without an ``end``
    row is interrupted (for example SIGKILL); it is never an execution checkpoint.
    """
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Polling interval must be finite and positive")
    receipt_path = Path(receipt_path)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)

    async def hold_own_generation():
        try:
            values = (await executor.client.get_state([executor.workcell]))["entities"][
                executor.workcell
            ]["values"]
            if (values["controlOwner"] != executor.owner
                    or values["controlEpoch"] != executor.control_epoch):
                return "already_fenced"
            await executor.client.invoke_action(executor.workcell, "requestControl", {
                "owner": executor.owner, "epoch": executor.control_epoch,
                "operationId": "interrupt-" + uuid.uuid4().hex,
                "nextOwner": "interrupted-" + executor.plan_id, "mode": "manual",
            }, wait=False, actor="cli:cell-passage")
            return "requested"
        except GatewayError:
            return "unconfirmed"

    with receipt_path.open("x", encoding="utf8") as receipt:
        def record(document):
            receipt.write(json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n")
            receipt.flush()

        record({"event": "start", "planId": executor.plan_id, "target": executor.target,
                "controlOwner": executor.owner, "controlEpoch": executor.control_epoch,
                "experimentQualified": False})
        try:
            previous = None
            while True:
                result = await executor.advance()
                # Retain state transitions, rather than a stream of identical polls.
                encoded = json.dumps(result, sort_keys=True, ensure_ascii=False)
                if encoded != previous:
                    record({"event": "observation", **result})
                    previous = encoded
                if result["status"] not in ("observing", "paused", "ready"):
                    break
                await asyncio.sleep(interval)
        except BaseException:
            hold = await hold_own_generation()
            record({"event": "end", "status": "interrupted", "active": executor.active,
                    "hold": hold, "experimentQualified": False})
            raise
        hold = None if result["status"] == "completed" else await hold_own_generation()
        record({"event": "end", "status": "plan_completed" if result["status"] == "completed"
                else "dispatch_stopped", "result": result, "active": executor.active,
                "hold": hold, "experimentQualified": False})
        return result


async def _main(args):
    from apps.cli.lw.client import GatewayClient

    if args.receipt.exists():
        raise ValueError("Receipt already exists; start a new round in a fresh output path")
    document = json.loads(args.document.read_text(encoding="utf8"))
    document["id"] = "passage-" + uuid.uuid4().hex
    async with GatewayClient(url=args.url, session=args.session) as client:
        plan = await client.plan_create(document, actor="cli:cell-passage")
        executor = await PassageExecutor.open(
            client, plan["id"], args.workcell, owner=args.owner, epoch=args.epoch,
            budget_seconds=args.budget_seconds,
        )
        result = await drive_executor(executor, args.receipt)
        print(json.dumps({"status": result["status"], "planId": plan["id"],
                          "receipt": str(args.receipt), "experimentQualified": False}))
        return 0 if result["status"] == "completed" else 3


def main():
    parser = argparse.ArgumentParser(description="Run a fresh Cell Passage Operation Plan through HTTP")
    parser.add_argument("--url", default="http://127.0.0.1:8023")
    parser.add_argument("--session", required=True)
    parser.add_argument("--document", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--workcell", default="/World/Lab/LiquidHandler")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--epoch", required=True, type=int)
    parser.add_argument("--budget-seconds", type=float, default=120)
    raise SystemExit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
