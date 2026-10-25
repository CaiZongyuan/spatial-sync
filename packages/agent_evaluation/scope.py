"""Case-scoped HTTP authority; ContextVars never come from caller headers."""

import asyncio
import json
import re
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import JSONResponse

from packages.info.plans import PlanError

PREFIX = "lw-case-"
_scope = ContextVar("external_evaluation_case", default=None)
_observer = ContextVar("external_evaluation_observer", default=False)


def current_scope():
    value = _scope.get()
    if value is not None:
        value.require()
    return value


def owns_session(session):
    value = current_scope()
    return bool(value and value.session is session)


def is_internal_observation(request):
    """Only a matching server-owned read is outside the public client quota."""
    grant = _scope.get()
    return bool(
        _observer.get() and grant is not None and request.method == "GET"
        and request.headers.get("authorization") == "Bearer " + grant.token
        and grant.permits(request.method, request.url.path, request.query_params, {})
    )


@contextmanager
def observation_scope(grant):
    marker = _observer.set(True)
    current = _scope.set(grant)
    try:
        yield
    finally:
        _scope.reset(current)
        _observer.reset(marker)


@dataclass
class CaseGrant:
    run_id: str
    case: dict
    session: object
    deadline: float
    audit: object
    token: str = field(default_factory=lambda: PREFIX + secrets.token_urlsafe(32))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    revoked: bool = False

    @property
    def actor_name(self):
        return "external-" + self.case["caseRunId"]

    def require(self):
        if (
            self.revoked
            or time.monotonic() >= self.deadline
            or self.session.state not in ("RUNNING", "PAUSED")
            or self.case["status"] != "running"
            and not _observer.get()
        ):
            raise PlanError("agent_scope_expired", "The case grant is no longer active", status=403)

    def permits(self, method, path, query, body):
        base = "/api/v1/sessions/" + self.session.session_id
        if path == base or path.startswith(base + "/"):
            suffix = path[len(base) :]
            if method == "GET":
                return suffix in (
                    "",
                    "/catalog",
                    "/state",
                    "/entities",
                    "/entity",
                    "/invocations",
                    "/wait",
                ) or bool(re.fullmatch(r"/invocations/inv-[a-f0-9]+", suffix))
            if method == "POST":
                if suffix == "/commands":
                    return body.get("command") in ("pause", "resume", "reset", "step")
                return suffix in ("/actions", "/check") or bool(
                    re.fullmatch(r"/invocations/inv-[a-f0-9]+/cancel", suffix)
                )
            return False
        if path == "/api/v1/plans":
            if method == "GET":
                return query.getlist("sessionId") == [self.session.session_id]
            return (
                method == "POST"
                and body.get("kind", "operation") == "operation"
                and body.get("target", "session") == "session"
                and body.get("sessionId") == self.session.session_id
            )
        match = re.fullmatch(r"/api/v1/plans/(plan-[A-Za-z0-9-]+)(/revisions)?", path)
        if match:
            record = self.session.plans.records.get(match[1])
            if (
                record is None
                or record.sessionId != self.session.session_id
                or record.sceneRevision != self.session.manifest.sceneRevision
            ):
                return False
            return (
                method == "GET"
                and not match[2]
                or (
                    method == "POST"
                    and bool(match[2])
                    and body.get("sessionId") == self.session.session_id
                    and body.get("kind", "operation") == "operation"
                    and body.get("target", "session") == "session"
                )
            )
        if path == "/api/v1/alerts" and method == "GET":
            return query.getlist("sessionId") == [self.session.session_id]
        match = re.fullmatch(r"/api/v1/alerts/([^/]+)/ack", path)
        return bool(match and method == "POST" and self.session.alerts and self.session.alerts.get(match[1]))


class CaseAuthority:
    def __init__(self, app, service):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", ()))
        authorization = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not token.startswith(PREFIX):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        marker = None
        try:
            grant = self.service.grants.get(token)
            if grant is None:
                raise PlanError("agent_scope_required", "A valid case grant is required", status=401)
            request = Request(scope, receive)
            raw = await request.body()
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict) or not grant.permits(
                request.method, request.url.path, request.query_params, body
            ):
                raise PlanError(
                    "agent_capability_denied", "This interface is not granted to Target", status=403
                )
            # Internal final observations use the same HTTP projections while the
            # completion fence holds this lock; Target cannot set this ContextVar.
            if _observer.get():
                grant.require()
                return await self.app(scope, receive, send)
            async with grant.lock:
                grant.require()
                marker = _scope.set(grant)
                record = grant.audit.begin(
                    request.method,
                    request.url.path,
                    request.query_params,
                    body,
                    request.headers.get("x-lw-tool-name"),
                )
                chunks, status, delivered = [], 500, False

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
                    if message["type"] == "http.response.body":
                        chunks.append(message.get("body", b""))
                    await send(message)

                try:
                    async with asyncio.timeout_at(grant.deadline):
                        await self.app(scope, replay, capture)
                finally:
                    grant.audit.end(record, status, b"".join(chunks))
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
                _scope.reset(marker)
