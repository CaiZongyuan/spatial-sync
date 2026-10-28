"""Cell Passage client scheduler. Device actions remain in the public HTTP pipeline.

This module deliberately does not change the ordinary ``lw plan run`` semantics.
Call ``advance`` repeatedly; paused and observing results are not action failures.
"""

from __future__ import annotations

import uuid

from apps.cli.lw.client import TERMINAL, GatewayError


class PassageExecutor:
    def __init__(self, client, plan, workcell, owner, epoch, material_revision):
        self.client, self.plan_id, self.revision = client, plan["id"], plan["revision"]
        self.workcell, self.owner, self.control_epoch = workcell, owner, epoch
        self.target = {name: plan[name] for name in ("sessionId", "sceneRevision", "epoch")}
        self.active = None
        self.blocked = None
        self.material_revision = material_revision

    @classmethod
    async def open(cls, client, plan_id, workcell, *, owner, epoch):
        plan = await client.plan_show(plan_id)
        if (plan.get("kind") != "operation" or plan.get("target") != "session"
                or plan["status"] not in ("approved", "running", "completed")
                or any(step["status"] not in ("pending", "done", "skipped") for step in plan["steps"])):
            raise ValueError("Inspect/revise the Plan; a new executor cannot resume in-flight work")
        values = (await client.get_state([workcell]))["entities"][workcell]["values"]
        return cls(client, plan, workcell, owner, epoch, values["materialRevision"])

    async def advance(self):
        """Observe once and dispatch at most one step, never replaying an uncertain POST."""
        plan = await self.client.plan_show(self.plan_id)
        session = await self.client.status()
        values = (await self.client.get_state([self.workcell]))["entities"][self.workcell]["values"]
        if any(session[name] != expected for name, expected in self.target.items()):
            self.blocked = "session_changed"
        if plan["revision"] != self.revision:
            self.blocked = "plan_revised"
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
                    step = next(row for row in plan["steps"] if row["id"] == step_id)
                    if invocation["status"] != "succeeded" or step["status"] == "failed":
                        return {"status": "failed", "stepId": step_id, "invocation": invocation}
                    if step["status"] == "done":
                        self.active = None
                        self.material_revision = values["materialRevision"]
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
            return {"status": "observing", "invocation": invocation}
        if plan["status"] == "completed":
            return {"status": "completed", "plan": plan}
        if not values["controlSafe"]:
            return {"status": "reconcile_required", "reason": "devices_not_safe"}
        step = next((row for row in plan["steps"] if row["status"] not in ("done", "skipped")), None)
        if step is None or step["status"] != "pending":
            return {"status": "reconcile_required", "reason": "step_not_pending"}
        if "wait" in step:
            raise ValueError("Use a workcell simTime wait action for pause-aware experimental waits")
        context = {"actor": "cli:cell-passage", "planId": self.plan_id,
                   "stepId": step["id"], "planRevision": self.revision}
        if "check" in step:
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
        self.active = {"stepId": step["id"], "requestId": uuid.uuid4().hex}
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
