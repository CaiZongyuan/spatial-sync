"""Durable Agent Jobs share the evaluator's one Session admission queue."""

import asyncio
import json
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from typing import Literal

import httpx
import psutil
from pydantic import Field

from apps.node.process import CleanupIncomplete, cleanup_confirmed
from packages.agents.artifacts import publish_case as public_case_artifacts
from packages.agents.audit import CaseAudit
from packages.agents.bundle import prepare_harness
from packages.agents.inputs import AgentPlanInputs, fingerprint
from packages.agents.provider import BudgetedProvider, ProviderLimits, usage_snapshot
from packages.agents.transport import reconcile_observations
from packages.info.agent_grading import final_observations, grade_agent_case
from packages.info.plans import PlanError
from packages.library.agents import agent_name
from packages.library.transactions import durable_json
from packages.protocol.internal import SessionOptions
from packages.training.schema import Schema, Text

TERMINAL = {"succeeded", "failed", "cancelled"}


class AgentRunRequest(Schema):
    suite: Text
    version: int | None = Field(default=None, ge=1)
    split: Literal["train", "test"] = "train"
    planId: Text | None = None
    planRevision: int | None = Field(default=None, ge=1)
    requestId: str | None = Field(default=None, min_length=1, max_length=200)


class AgentRuns:
    def __init__(self, manager, agents, adapter, app):
        self.manager, self.agents, self.adapter, self.app = manager, agents, adapter, app
        self.inputs = AgentPlanInputs(manager, agents, adapter.describe)
        self.path = manager.settings.state_root / "agent-runs.json"
        self.root = (manager.settings.state_root / "agent-runs").resolve()
        self.records, self._deadlines, self._case_active = {}, {}, {}
        self._audits = {}
        self._lock = threading.RLock()
        self._failure = False
        self.clock = time.time
        self.instance = uuid.uuid4().hex

    def startup(self):
        if self.path.exists():
            self.records = json.loads(self.path.read_bytes())
        for record in list(self.records.values()):
            started_unsettled = record.get("startedAt") is not None and record["status"] not in TERMINAL
            committed = next(
                (
                    row
                    for row in self.manager.evaluations.store.rows
                    if row["evaluationId"] == record["evaluationId"]
                ),
                None,
            )
            if committed is not None:
                self.update(record["jobId"], status="succeeded", result=committed, reason=None)
            elif record["status"] not in TERMINAL:
                self.update(
                    record["jobId"], status="failed", reason="gateway_restarted", finishedAt=time.time()
                )
            confirmed = self.resources_confirmed(record)
            if (record.get("cleanupIncomplete") or started_unsettled) and self.owner_alive_or_unknown(record):
                confirmed = False
            if not confirmed:
                self.update(record["jobId"], cleanupIncomplete=True)
                self.manager.evaluations._blocked.add(record["evaluationId"])
            else:
                if record.get("cleanupIncomplete"):
                    self.update(record["jobId"], cleanupIncomplete=False)
            # Prefix observations increase retained actual usage; they never clear
            # an owner fence or grant work, even while old cleanup is unconfirmed.
            self.reconcile_usage(record["jobId"])

    def update(self, identity, **changes):
        with self._lock:
            if self._failure:
                raise PlanError("agent_runs_unavailable", "Agent Job persistence is unavailable", status=503)
            record = deepcopy(self.records[identity]) | deepcopy(changes)
            records = self.records | {identity: record}
            try:
                durable_json(self.path, records)
            except OSError as error:
                self._failure = True
                raise PlanError(
                    "agent_runs_unavailable", "Agent Job persistence is unavailable", status=503
                ) from error
            self.records = records
            return record

    def create(self, record):
        with self._lock:
            if self._failure:
                raise PlanError("agent_runs_unavailable", "Agent Job persistence is unavailable", status=503)
            records = self.records | {record["jobId"]: deepcopy(record)}
            try:
                durable_json(self.path, records)
            except OSError as error:
                self._failure = True
                raise PlanError(
                    "agent_runs_unavailable", "Agent Job persistence is unavailable", status=503
                ) from error
            self.records = records
            return records[record["jobId"]]

    def get(self, identity):
        if identity not in self.records:
            raise PlanError("unknown_job", "Unknown Agent Job", status=404)
        return self.records[identity]

    def publish_case(self, identity, case_id):
        record = self.get(identity)
        artifacts = {value["id"]: value for value in record["artifacts"]}
        selected = public_case_artifacts(self.root, record, case_id)
        artifacts.update({value["id"]: value for value in selected})
        self.update(identity, artifacts=list(artifacts.values()))
        return selected

    def append_log(self, identity, case_id, record):
        path = self.root / identity / "public.log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        message = json.dumps({"caseRunId": case_id, **record}, ensure_ascii=False, allow_nan=False)
        with path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(message, ensure_ascii=False) + "\n")

    async def logs(self, identity, after, timeout):
        deadline = time.monotonic() + timeout
        while True:
            record = self.get(identity)
            path = self.root / identity / "public.log.jsonl"
            lines = (
                [
                    json.loads(line)
                    for line in path.read_bytes().splitlines(keepends=True)
                    if line.endswith(b"\n")
                ]
                if path.exists()
                else []
            )
            if after > len(lines):
                raise PlanError(
                    "invalid_log_cursor", "Log cursor is beyond retained public output", status=422
                )
            complete = record["status"] in TERMINAL and not record.get("cleanupIncomplete")
            if len(lines) > after or complete or time.monotonic() >= deadline:
                selected = lines[after : after + 500]
                return {
                    "lines": selected,
                    "cursor": after + len(selected),
                    "complete": complete and after + len(selected) == len(lines),
                }
            await asyncio.sleep(0.05)

    def document(self, record):
        result = deepcopy(
            {
                key: value
                for key, value in record.items()
                if key
                not in {"provider", "requestHash", "frozenInputs", "plan", "authorityId", "executionOwner"}
            }
        )
        result["usage"] = usage_snapshot(record["provider"]["requests"], record["provider"].get("stopReason"))
        return result

    @staticmethod
    def owner_alive_or_unknown(record):
        owner = record.get("executionOwner")
        if not owner:
            return True
        try:
            return psutil.Process(owner["pid"]).create_time() == owner["createdAt"]
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True

    def resources_confirmed(self, record):
        for receipt in (self.root / record["jobId"]).glob("**/process-*.json"):
            if not cleanup_confirmed(receipt.parent, json.loads(receipt.read_bytes())):
                return False
        return True

    def reconcile_usage(self, identity):
        record = self.get(identity)
        path = self.root / identity / "private-transport" / "observations.jsonl"
        if not path.exists():
            return
        budget = record["budget"]
        gate = None

        def save():
            self.update(identity, provider=gate.state, usage=gate.snapshot())

        gate = BudgetedProvider(
            identity,
            record["model"].split("/", 1)[1],
            ProviderLimits(
                budget["requests"],
                budget["tokens"],
                budget["llmUsd"],
                budget["wallSeconds"],
                budget["maxOutputTokensPerRequest"],
            ),
            None,
            None,
            deepcopy(record["provider"]),
            save,
            None,
            "",
            transport_token="",
            is_active=lambda: False,
        )
        reconcile_observations(path, gate)

    async def cleanup(self, identity):
        record = self.get(identity)
        owner = record.get("executionOwner") or {}
        if (
            record.get("cleanupIncomplete")
            and owner.get("instance") != self.instance
            and self.owner_alive_or_unknown(record)
        ):
            raise PlanError(
                "agent_cleanup_unconfirmed", "The previous Agent owner is still alive or unknown", status=409
            )
        confirm = getattr(self.adapter, "confirm_cleanup", None)
        if record.get("cleanupIncomplete") and (confirm is None or not await confirm(identity)):
            raise PlanError(
                "agent_cleanup_unconfirmed", "Owned Agent resources are not yet confirmed closed", status=409
            )
        if not self.resources_confirmed(record):
            raise PlanError(
                "agent_cleanup_unconfirmed", "Owned Agent process cleanup is unconfirmed", status=409
            )
        self.reconcile_usage(identity)
        self.update(identity, cleanupIncomplete=False)
        return self.document(self.get(identity))

    def plan_in_use(self, identity, revision=None):
        return any(
            record["planId"] == identity
            and (revision is None or record["planRevision"] == revision)
            and (record["status"] not in TERMINAL or record.get("cleanupIncomplete"))
            for record in self.records.values()
        )

    async def cancel(self, identity):
        evaluations = self.manager.evaluations
        async with evaluations._admission:
            record = self.get(identity)
            if record["status"] in TERMINAL:
                return self.document(record)
            self.update(identity, status="cancelling", reason="cancel_requested")
            self.manager.agent_grants.revoke(identity)
            await evaluations.cancel_evaluation(record["evaluationId"])
            if record["status"] == "queued":
                self.update(identity, status="cancelled", finishedAt=time.time(), reason="cancelled")
            return self.document(self.get(identity))

    async def request(self, identifier, body, actor):
        from apps.api.app.evaluations import now, persist

        agent = "agent/" + agent_name(identifier)
        request_id = body.requestId or uuid.uuid4().hex
        request_hash = fingerprint({"agent": agent, **body.model_dump(exclude={"requestId"})})
        evaluations = self.manager.evaluations
        async with evaluations._admission:
            for existing in self.records.values():
                if (existing["requestId"], existing["agent"], existing["requestedBy"]) == (
                    request_id,
                    agent,
                    actor.model_dump(),
                ):
                    if existing["requestHash"] != request_hash:
                        raise PlanError(
                            "idempotency_conflict", "Request ID belongs to different Agent inputs", status=409
                        )
                    return self.document(existing)
            if body.split != "train":
                raise PlanError(
                    "test_requires_promotion", "Held-out evaluation requires promotion", status=403
                )
            if body.planId is None or body.planRevision is None:
                raise PlanError(
                    "approved_agent_plan_required", "Select an approved Agent Plan revision", status=409
                )
            stored = self.manager.training_plans.get(body.planId)
            plan = self.manager.training_plans.document(stored)
            if (
                plan.get("layer") != "agent"
                or plan["status"] != "approved"
                or plan["revision"] != body.planRevision
            ):
                raise PlanError(
                    "approved_agent_plan_required",
                    "Select the current approved Agent Plan revision",
                    status=409,
                )
            if (
                agent != plan["agent"]["id"]
                or body.suite != plan["suite"]
                or body.version not in (None, plan["agent"]["harnessVersion"])
            ):
                raise PlanError(
                    "agent_input_mismatch", "Agent run inputs differ from the approved Plan", status=409
                )
            if self.plan_in_use(plan["id"]):
                raise PlanError("agent_plan_busy", "This Plan already has an admitted Agent Run", status=409)
            frozen = self.inputs.verify(stored)
            goal = self.manager.loop_budgets.summary(plan["goalId"])
            exception = self.goal_exception(stored)
            if (
                goal["deadline"] is not None
                and self.clock() >= goal["deadline"]
                and "days" not in (exception or {}).get("dimensions", [])
            ):
                raise PlanError("goal_budget_exhausted", "The Goal deadline expired", status=409)
            # The fresh human approval grants this finite envelope, including any
            # overage shown on its card, without changing the original Goal limit.
            peers = [value for value in self.records.values() if value["planId"] == plan["id"]]
            used_cases = sum(value["attemptedCaseRuns"] for value in peers)
            required = frozen["caseRunsRequired"]
            if used_cases + required > plan["budget"]["caseRuns"]:
                raise PlanError(
                    "agent_case_budget_exhausted",
                    "The Plan cannot cover another complete train scope",
                    status=409,
                )
            budget = dict(plan["budget"])
            budget["wallSeconds"] -= sum(self.wall_commitment(value) for value in peers)
            if budget["wallSeconds"] <= 0:
                raise PlanError(
                    "agent_wall_budget_exhausted", "The Plan's execution seconds are exhausted", status=409
                )
            for name, known, reserved in (
                ("requests", "knownRequests", "reservedRequests"),
                ("tokens", "knownTokens", "reservedTokens"),
                ("llmUsd", "knownLlmUsd", "reservedUsd"),
            ):
                consumed = sum(
                    (
                        Decimal(str(value["usage"].get(known, 0)))
                        + Decimal(str(value["usage"].get(reserved, 0)))
                        for value in peers
                    ),
                    Decimal(0),
                )
                remaining = Decimal(str(budget[name])) - consumed
                budget[name] = float(remaining) if name == "llmUsd" else int(remaining)
            context = frozen["adapter"]["contextTokens"]
            quote = self.inputs.quote(plan["model"]["ref"])
            if (
                budget["requests"] < 1
                or budget["tokens"] < context + budget["maxOutputTokensPerRequest"]
                or Decimal(str(budget["llmUsd"])) < quote.bound(context, budget["maxOutputTokensPerRequest"])
            ):
                raise PlanError(
                    "agent_budget_exhausted", "The next model request cannot be reserved", status=409
                )
            identity, evaluation_id = "job-" + uuid.uuid4().hex, "eval-" + uuid.uuid4().hex
            record = {
                "jobId": identity,
                "kind": "agent-run",
                "layer": "agent",
                "status": "admitting",
                "reason": None,
                "requestId": request_id,
                "requestHash": request_hash,
                "requestedBy": actor.model_dump(),
                "agent": agent,
                "harnessVersion": plan["agent"]["harnessVersion"],
                "planId": plan["id"],
                "planRevision": plan["revision"],
                "authorityId": stored.approvalId,
                "goalId": plan["goalId"],
                "suite": plan["suite"],
                "split": "train",
                "evaluationId": evaluation_id,
                "model": plan["model"]["ref"],
                "thinking": plan["model"]["thinking"],
                "executionDevice": "cpu",
                "nodeId": None,
                "placement": "gateway-local",
                "gpuHours": 0,
                "createdAt": time.time(),
                "startedAt": None,
                "finishedAt": None,
                "attemptedCaseRuns": 0,
                "allocatedCaseRuns": required,
                "allocatedLlmUsd": budget["llmUsd"],
                "allocatedWallSeconds": budget["wallSeconds"],
                "executionSeconds": 0.0,
                "cleanupSeconds": 0.0,
                "goalDeadline": goal["deadline"],
                "goalException": exception,
                "budget": budget,
                "plan": plan,
                "frozenInputs": frozen,
                "provider": {"requests": []},
                "usage": {
                    "knownRequests": 0,
                    "knownTokens": 0,
                    "knownLlmUsd": 0.0,
                    "reservedRequests": 0,
                    "reservedTokens": 0,
                    "reservedUsd": 0.0,
                    "requests": 0,
                    "tokens": 0,
                    "llmUsd": 0.0,
                    "unknownUsageRequests": 0,
                },
                "caseRuns": [],
                "artifacts": [],
                "admissionState": "creating-evaluation",
            }
            try:
                await persist(self.create, record)
            except asyncio.CancelledError:
                self.update(identity, status="failed", reason="admission_interrupted", finishedAt=time.time())
                raise
            evaluation = {
                "evaluationId": evaluation_id,
                "layer": "agent",
                "jobId": identity,
                "suite": plan["suite"],
                "suiteContentHash": frozen["suite"]["contentHash"],
                "subject": agent,
                "split": "train",
                "status": "queued",
                "at": now(),
                "planId": plan["id"],
                "planRevision": plan["revision"],
                "goalId": plan["goalId"],
                "allocatedCaseRuns": required,
                "attemptedCaseRuns": 0,
                "caseRuns": [],
            }
            try:
                await persist(evaluations.store.save, evaluation)
                await persist(lambda: self.update(identity, status="queued", admissionState="queued"))
            except BaseException as error:
                self.update(identity, status="failed", reason="admission_interrupted", finishedAt=time.time())
                if evaluation_id in evaluations.store.records:
                    evaluations.store.save(
                        evaluation | {"status": "failed", "error": {"code": "admission_interrupted"}}
                    )
                if isinstance(error, OSError):
                    raise PlanError(
                        "agent_admission_unavailable", "Agent admission could not be committed", status=503
                    ) from error
                raise
            evaluations._queue.append(("evaluation", evaluation_id))
            evaluations.wake()
            return self.document(self.get(identity))

    def active(self, identity):
        record = self.get(identity)
        approval = self.manager.approvals.records.get(record["authorityId"])
        proof = record.get("goalException") or {}
        days_exception = (
            "days" in proof.get("dimensions", [])
            and approval is not None
            and proof.get("approvalId") == record["authorityId"]
            and proof.get("planId") == record["planId"]
            and proof.get("planRevision") == record["planRevision"]
            and approval.details.get("loopBudget", {}).get("basis") == proof.get("reviewBasis")
            and "days" in approval.details.get("loopBudget", {}).get("exhaustedDimensions", [])
        )
        return (
            not self._failure
            and record["status"] == "running"
            and self._case_active.get(identity, True)
            and (identity not in self._audits or self._audits[identity].healthy)
            and approval
            and approval.status == "approved"
            and time.monotonic() < self._deadlines.get(identity, 0)
            and (record["goalDeadline"] is None or self.clock() < record["goalDeadline"] or days_exception)
        )

    def goal_exception(self, stored):
        approval = self.manager.approvals.records.get(stored.approvalId)
        review = (stored.goalContext or {}).get("review", {})
        card = approval.details.get("loopBudget", {}) if approval else {}
        human = (
            next(
                (
                    decision
                    for decision in reversed(approval.decisions)
                    if decision.decision == "approve" and decision.actor.kind != "agent"
                ),
                None,
            )
            if approval
            else None
        )
        if (
            not approval
            or approval.status != "approved"
            or human is None
            or card.get("basis") != review.get("basis")
            or not review.get("basis")
        ):
            return None
        dimensions = [
            name
            for name in ("caseRuns", "llmUsd")
            if (review.get("limit") or {}).get(name) is not None
            and review["effective"].get(name, 0) > review["limit"][name]
        ]
        if "days" in review.get("exhaustedDimensions", []) and review.get("deadline") is not None:
            dimensions.append("days")
        if not dimensions:
            return None
        return {
            "dimensions": dimensions,
            "approvalId": approval.approvalId,
            "planId": stored.plan.id,
            "planRevision": stored.plan.revision,
            "reviewBasis": review["basis"],
            "originalDeadline": review.get("deadline"),
            "approvedAtMs": human.atMs,
        }

    @staticmethod
    def wall_commitment(record):
        allocated = record.get("allocatedWallSeconds", record["budget"]["wallSeconds"])
        if record["status"] not in TERMINAL or record.get("cleanupIncomplete"):
            return allocated
        if record.get("startedAt") is None:
            return 0.0
        if record.get("executionSeconds") is not None:
            return record["executionSeconds"]
        return allocated  # Unknown/restarted execution does not manufacture free seconds.

    @asynccontextmanager
    async def execution_transport(self, record, started):
        ended = None
        try:
            async with self.adapter.open(
                record, self._deadlines[record["jobId"]], self.root / record["jobId"]
            ) as transport:
                try:
                    yield transport
                finally:
                    ended = time.monotonic()
        finally:
            now = time.monotonic()
            execution = min(record["allocatedWallSeconds"], max(0, (ended or now) - started))
            self.update(
                record["jobId"],
                executionSeconds=execution,
                cleanupSeconds=max(0, now - ended) if ended is not None else None,
                executionTiming="measured" if ended is not None else "conservative-upper-bound",
            )

    async def evaluate(self, evaluation_id):
        from lw.scoped import ScopedGatewayClient

        from apps.api.app.evaluations import now, persist, runtime_fingerprint

        evaluations = self.manager.evaluations
        evaluation = deepcopy(evaluations.store.records[evaluation_id])
        identity = evaluation["jobId"]
        original = self.get(identity)
        plan = original["plan"]
        started = time.monotonic()
        self._deadlines[identity] = started + original["budget"]["wallSeconds"]
        gate, transport, session, audit = None, None, None, None
        current_case = None
        try:
            owner = psutil.Process()
            self.update(
                identity,
                status="running",
                startedAt=time.time(),
                admissionState="running",
                executionSeconds=None,
                cleanupSeconds=None,
                executionOwner={
                    "pid": owner.pid,
                    "createdAt": owner.create_time(),
                    "instance": self.instance,
                },
            )
            evaluation.update(status="running", startedAt=now())
            await persist(evaluations.store.save, evaluation)
            self.inputs.verify(self.manager.training_plans.get(plan["id"], plan["revision"]))
            harness = prepare_harness(self.agents, original["agent"], original["harnessVersion"])
            suite = evaluations.suites.resolve(plan["suite"])
            if runtime_fingerprint() != evaluations._runtime_hash:
                raise PlanError("agent_inputs_changed", "Evaluation runtime changed", status=409)
            async with self.execution_transport(original, started) as transport:
                limits = ProviderLimits(
                    original["budget"]["requests"],
                    original["budget"]["tokens"],
                    original["budget"]["llmUsd"],
                    max(0.001, self._deadlines[identity] - time.monotonic()),
                    original["budget"]["maxOutputTokensPerRequest"],
                )

                def save_usage():
                    current = self.get(identity).get("currentCase")
                    if current is not None:
                        for row in gate.state["requests"]:
                            row.setdefault("caseRunId", current["caseRunId"])
                    self.update(identity, provider=gate.state, usage=gate.snapshot())

                def observe_model(kind, data):
                    current = self._audits.get(identity)
                    if current is not None:
                        current.append(kind, data)

                gate = BudgetedProvider(
                    identity,
                    plan["model"]["ref"].split("/", 1)[1],
                    limits,
                    lambda: self.inputs.quote(plan["model"]["ref"]),
                    original["frozenInputs"]["adapter"]["contextTokens"],
                    deepcopy(original["provider"]),
                    save_usage,
                    transport.client,
                    transport.grant,
                    transport_token=secrets.token_urlsafe(32),
                    is_active=lambda: bool(self.active(identity)),
                    trusted_relay=transport.trusted_relay,
                    observe=observe_model,
                )
                for case in suite.public()["cases"]:
                    for repetition in range(1, suite.metadata["runsPerCase"] + 1):
                        self._case_active[identity] = True
                        if not self.active(identity):
                            raise PlanError(
                                "agent_run_inactive", "Agent authority or deadline expired", status=403
                            )
                        suite.verify()
                        current = self.get(identity)
                        number = current["attemptedCaseRuns"] + 1
                        case_id = f"case-{number:04d}"
                        current_case = {
                            "caseRunId": case_id,
                            "caseId": case["id"],
                            "repetition": repetition,
                            "status": "attempting",
                        }
                        self.update(identity, attemptedCaseRuns=number, currentCase=current_case)
                        evaluation["attemptedCaseRuns"] = number
                        await persist(evaluations.store.save, evaluation)
                        session = await self.manager.create(
                            None,
                            original["frozenInputs"]["suite"]["source"],
                            SessionOptions(
                                continueWithoutObservers=True,
                                approval={"high": "require"},
                                **original["frozenInputs"]["adapter"].get("sessionOptions", {}),
                            ),
                            scene_version=original["frozenInputs"]["suite"]["scene"],
                            evaluation_id=evaluation_id,
                        )
                        session.agent_run_id, session.deny_human_gates = identity, True
                        directory = self.root / identity / "cases" / case_id
                        audit = CaseAudit(
                            session,
                            directory / "trace.jsonl",
                            on_record=lambda record: self.append_log(identity, case_id, record),
                        )
                        self._audits[identity] = audit
                        session.agent_audit = audit
                        grant = self.manager.agent_grants.issue(
                            identity,
                            session.session_id,
                            audit=audit,
                            active=lambda: bool(self.active(identity)) and audit.healthy,
                            ttl=max(0.01, self._deadlines[identity] - time.monotonic()),
                        )
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(self.app), base_url="http://127.0.0.1"
                        ) as http:
                            gateway = ScopedGatewayClient(
                                run_id=identity,
                                session=session.session_id,
                                token=grant,
                                url="http://127.0.0.1",
                                http=http,
                            )
                            task = {
                                "caseId": case["id"],
                                "statement": case["statement"],
                                "directory": directory,
                                "deadline": self._deadlines[identity],
                                "harness": harness,
                                "model": gate.model,
                                "thinking": plan["model"]["thinking"],
                                "runId": identity,
                                "sessionId": session.session_id,
                                "grant": grant,
                                "gatewayUrl": self.manager.settings.node_gateway_url,
                                "limits": asdict(limits),
                                "onCleanup": lambda: self._case_active.__setitem__(identity, False),
                            }
                            try:
                                async with asyncio.timeout(
                                    max(0.001, self._deadlines[identity] - time.monotonic())
                                ):
                                    await self.adapter.case(task, gateway, gate)
                            finally:
                                self._case_active[identity] = False
                            final = await http.get(f"/api/v1/sessions/{session.session_id}/state")
                            summary = (await http.get(f"/api/v1/sessions/{session.session_id}")).json()
                            alerts = (
                                await http.get("/api/v1/alerts", params={"sessionId": session.session_id})
                            ).json()["alerts"]
                            audit.seal(
                                {"state": final.json(), "session": summary, "alerts": alerts},
                                observed_monotonic=float(final.headers["X-LW-Observed-Monotonic"]),
                            )
                            values, metadata = final_observations(final.json(), summary, alerts)
                            elapsed = time.monotonic() - audit.started
                            outcome = grade_agent_case(
                                suite, case["id"], values, audit.derived(), elapsed, metadata
                            )
                        self.manager.agent_grants.revoke(identity)
                        await session.close()
                        session.agent_audit = None
                        audit.close()
                        if not audit.healthy:
                            raise PlanError(
                                "agent_trace_unavailable", "Trusted case evidence is unavailable", status=503
                            )
                        if hasattr(transport, "reconcile"):
                            transport.reconcile(gate)
                        trace_id = f"trace-{identity}-{case_id}"
                        outcome.update(
                            caseRunId=case_id,
                            caseId=case["id"],
                            repetition=repetition,
                            status="valid",
                            traceId=trace_id,
                            recordingId=session.session_id,
                            elapsedSeconds=elapsed,
                        )
                        durable_json(directory / "final.json", {"state": final.json(), "session": summary})
                        durable_json(directory / "score.json", outcome)
                        runs = self.get(identity)["caseRuns"] + [outcome]
                        self.update(identity, caseRuns=runs, currentCase=None)
                        rows = [row for row in gate.state["requests"] if row.get("caseRunId") == case_id]
                        durable_json(
                            directory / "usage.json",
                            {
                                "source": "trusted-transport",
                                "jobId": identity,
                                "caseRunId": case_id,
                                "summary": usage_snapshot(rows),
                                "attempts": rows,
                            },
                        )
                        self.publish_case(identity, case_id)
                        evaluation["caseRuns"] = runs
                        await persist(evaluations.store.save, evaluation)
                        session, audit = None, None
                        self._audits.pop(identity, None)
                        current_case = None
                if len(self.get(identity)["caseRuns"]) != original["allocatedCaseRuns"]:
                    raise PlanError(
                        "agent_evaluation_incomplete", "Required repetitions are incomplete", status=503
                    )
            if hasattr(transport, "reconcile"):
                transport.reconcile(gate)
            cases = self.get(identity)["caseRuns"]
            usage = self.get(identity)["usage"]
            row = {
                "evaluationId": evaluation_id,
                "layer": "agent",
                "suite": plan["suite"],
                "subject": original["agent"],
                "version": original["harnessVersion"],
                "split": "train",
                "score": sum(case["score"] for case in cases) / len(cases),
                "ci95": None,
                "decision": "reported",
                "reference": None,
                "runs": suite.metadata["runsPerCase"],
                "caseRuns": len(cases),
                "traces": [case["traceId"] for case in cases],
                "recordings": [case["recordingId"] for case in cases],
                "at": now(),
                "durationSeconds": time.monotonic() - started,
                "planId": plan["id"],
                "planRevision": plan["revision"],
                "goalId": plan["goalId"],
                "key": {
                    "suite": suite.id,
                    "suiteContentHash": suite.content_hash,
                    "scene": suite.metadata["scene"],
                    "sceneContentHash": suite.metadata["sceneContentHash"],
                    "source": suite.metadata["source"],
                    "model": plan["model"]["ref"],
                    "thinking": plan["model"]["thinking"],
                    "runtime": original["frozenInputs"]["adapter"],
                },
                "cost": {"llmUsd": usage["llmUsd"], "tokens": usage["tokens"], "gpuHours": 0},
            }
            await persist(evaluations.store.append, row)
            evaluation.update(status="completed", result=row, endedAt=now())
            self.update(identity, status="succeeded", finishedAt=time.time(), result=row)
        except BaseException as error:
            self.manager.agent_grants.revoke(identity)
            resources = getattr(self.adapter, "release_confirmed", None)
            if isinstance(error, CleanupIncomplete) or resources is not None and not resources(identity):
                evaluations._blocked.add(evaluation_id)
                self.update(identity, cleanupIncomplete=True)
            if session is not None:
                try:
                    await session.close()
                except BaseException:
                    evaluations._blocked.add(evaluation_id)
                    self.update(identity, cleanupIncomplete=True)
                session.agent_audit = None
            if audit is not None:
                audit.close()
            if transport is not None and gate is not None and hasattr(transport, "reconcile"):
                transport.reconcile(gate)
            code = (
                error.detail.get("code", "agent_run_failed")
                if isinstance(error, PlanError)
                else "agent_run_failed"
            )
            cancelled = isinstance(error, asyncio.CancelledError)
            if cancelled:
                code = "cancelled"
            committed = next(
                (row for row in evaluations.store.rows if row["evaluationId"] == evaluation_id), None
            )
            if committed is not None:
                self.update(
                    identity, status="succeeded", finishedAt=time.time(), reason=None, result=committed
                )
                evaluation.update(status="completed", result=committed, endedAt=now())
            else:
                if current_case is not None:
                    outcome = current_case | {
                        "status": "cancelled" if cancelled else "invalid",
                        "error": {"code": code},
                    }
                    if session is not None:
                        outcome["recordingId"] = session.session_id
                    if audit is not None:
                        outcome["traceId"] = f"trace-{identity}-{current_case['caseRunId']}"
                    runs = self.get(identity)["caseRuns"] + [outcome]
                    self.update(identity, caseRuns=runs, currentCase=None)
                    self.publish_case(identity, current_case["caseRunId"])
                    evaluation["caseRuns"] = runs
                self.update(
                    identity,
                    status="cancelled" if cancelled else "failed",
                    reason=code,
                    finishedAt=time.time(),
                )
                evaluation.update(
                    status="cancelled" if cancelled else "failed", error={"code": code}, endedAt=now()
                )
        finally:
            await persist(evaluations.store.save, evaluation)
            self._deadlines.pop(identity, None)
            self._case_active.pop(identity, None)
            self._audits.pop(identity, None)

    async def close(self):
        for identity in self.records:
            self.manager.agent_grants.revoke(identity)
