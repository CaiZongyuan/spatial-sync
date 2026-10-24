"""Small workflow grants enforce the exact interfaces given to external conversations."""

import asyncio
import json
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import JSONResponse

from packages.info.plans import PlanError
from packages.library.agents import agent_name

_current = ContextVar("agent_rsi_principal", default=None)


def current_principal():
    value = _current.get()
    if value is not None:
        value.require()
    return value


@dataclass
class WorkflowGrant:
    service: object
    kind: str
    agent_id: str
    goal_id: str
    deadline: float
    native_session_id: str | None = None
    promotion_id: str | None = None
    reference_evaluation_id: str | None = None
    reference_version: int | None = None
    identifier: str = field(default_factory=lambda: secrets.token_hex(12))
    token: str = field(default="")
    revoked: bool = False

    def __post_init__(self):
        prefix = "lw-rsi-opt-" if self.kind == "optimizer" else "lw-rsi-eval-"
        self.token = prefix + secrets.token_urlsafe(32)

    @property
    def actor_name(self):
        return self.kind + "-" + self.identifier

    def require(self):
        if self.revoked or time.monotonic() >= self.deadline:
            raise PlanError("agent_scope_expired", "Workflow permission is no longer active", status=403)
        if self.service.audit_error:
            raise PlanError("agent_trace_unavailable", "Workflow Trace is unavailable", status=503)

    def own_plan(self, identity):
        record = self.service.records.get(identity)
        return bool(record and record.plan.agentId == self.agent_id and record.plan.goalId == self.goal_id)

    def permits(self, method, path, query, body):
        if self.kind == "promotion-controller":
            base = "/api/v1/agent-promotions/" + self.promotion_id
            if method == "GET":
                return path == base
            if method != "POST":
                return False
            return path in (
                base + "/next",
                base + "/complete",
                base + "/bind-native",
                base + "/runtime-records/import",
                base + "/cancel",
                base + "/cleanup",
            )
        base = "/api/v1/agents/" + agent_name(self.agent_id)
        if method == "GET":
            if path in (base, base + "/rsi", base + "/versions"):
                return True
            if re.fullmatch(re.escape(base) + r"/versions/[1-9][0-9]*(?:/snapshot)?", path) or re.fullmatch(
                re.escape(base) + r"/harness/[1-9][0-9]*", path
            ):
                return True  # Only confined public credential-free Harness files are served here.
            if path == "/api/v1/suites" or re.fullmatch(
                r"/api/v1/suites/[a-z0-9-]+/[1-9][0-9]*(?:/scoreboard)?", path
            ):
                return True  # These existing projections contain public train only.
            if path in ("/api/v1/notebooks/" + self.goal_id, "/api/v1/loops/" + self.goal_id):
                return True
            if path == "/api/v1/cost/summary":
                return query.getlist("goal") == [self.goal_id]
            if path == base + "/tool-trace":
                return query.getlist("goal") == [self.goal_id]
            if path == "/api/v1/agent-runs":
                return query.getlist("agent") == [self.agent_id]
            match = re.fullmatch(
                r"/api/v1/agent-runs/(eval-[a-f0-9]+)(?:/cases/(case-[a-f0-9]+)/artifacts/(trace|final|score))?",
                path,
            )
            if match:
                record = self.service.manager.evaluations.store.records.get(match[1])
                return bool(
                    record
                    and record.get("split") == "train"
                    and record.get("agentId") == self.agent_id
                    and (not match[2] or any(case["caseRunId"] == match[2] for case in record["cases"]))
                )
            match = re.fullmatch(r"/recordings/([^/]+)/.+", path)
            if match:
                return match[1] in self.service.public_recordings(self.agent_id)
            if path.startswith("/api/v1/plans/"):
                identity = path.removeprefix("/api/v1/plans/")
                return "/" not in identity and self.own_plan(identity)
            return False
        if method != "POST":
            return False
        if path == base + "/candidates":
            return body.get("referenceVersion") == self.reference_version
        if path == "/api/v1/plans":
            return (
                body.get("kind") == "training"
                and body.get("layer") == "agent"
                and body.get("agentId") == self.agent_id
                and body.get("goalId") == self.goal_id
                and body.get("referenceEvaluationId") == self.reference_evaluation_id
            )
        if path.startswith("/api/v1/plans/"):
            parts = path.removeprefix("/api/v1/plans/").split("/")
            if len(parts) != 2 or not self.own_plan(parts[0]):
                return False
            if parts[1] == "revisions":
                return (
                    body.get("layer") == "agent"
                    and body.get("agentId") == self.agent_id
                    and body.get("goalId") == self.goal_id
                )
            return parts[1] in ("agent-runs", "settle", "contamination", "promotions")
        return False


class WorkflowAuthority:
    def __init__(self, app, service):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", ()))
        authorization = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not token.startswith("lw-rsi-"):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        marker = None
        grant = None
        try:
            grant = self.service.grants.get(token)
            if grant is None:
                raise PlanError("agent_scope_required", "A valid workflow grant is required", status=401)
            grant.require()
            request = Request(scope, receive)
            audit_id = secrets.token_hex(16)
            raw = await request.body()
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                raise PlanError("invalid_agent_request", "Submit a JSON object", status=422)
            from packages.agent_rsi.contamination import observe_input

            try:
                await observe_input(
                    self.service,
                    grant,
                    {"body": body, "path": request.url.path, "query": request.query_params.multi_items()},
                    audit_id,
                )
            except PlanError as error:
                self.service.record_tool(
                    grant, request.method, request.url.path, error.status, request_id=audit_id
                )
                raise
            if not grant.permits(request.method, request.url.path, request.query_params, body):
                self.service.record_tool(grant, request.method, request.url.path, 403, request_id=audit_id)
                raise PlanError("agent_capability_denied", "This interface is not granted", status=403)
            marker = _current.set(grant)
            delivered = False
            status = 500

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": raw, "more_body": False}
                return await receive()

            async def capture(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                await send(message)

            try:
                self.service.record_tool(grant, request.method, request.url.path, None, request_id=audit_id)
                async with asyncio.timeout_at(grant.deadline):
                    await self.app(scope, replay, capture)
            except PlanError as error:
                status = error.status
                raise
            except TimeoutError:
                status = 403
                raise
            finally:
                try:
                    self.service.record_tool(
                        grant, request.method, request.url.path, status, request_id=audit_id
                    )
                except PlanError:
                    self.service.audit_error = (
                        True  # Existing mutation facts stay committed; future calls fail closed.
                    )
        except TimeoutError:
            await JSONResponse({"detail": {"code": "agent_scope_expired"}}, status_code=403)(
                scope, receive, send
            )
        except (ValueError, UnicodeError):
            await JSONResponse({"detail": {"code": "invalid_agent_request"}}, status_code=422)(
                scope, receive, send
            )
        except PlanError as error:
            await JSONResponse({"detail": error.detail}, status_code=error.status)(scope, receive, send)
        finally:
            if marker is not None:
                _current.reset(marker)
