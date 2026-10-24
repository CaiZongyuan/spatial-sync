"""Short-lived managed authority on the HTTP pipeline, independent of X-Actor."""

import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi.responses import JSONResponse

from packages.info.plans import PlanError

PREFIX = "lw-agent-"
_current = ContextVar("managed_agent_scope", default=None)


@dataclass(frozen=True)
class ManagedScope:
    run_id: str
    session_id: str
    revision: str
    evaluation_id: str | None
    actor_name: str
    valid: object

    def require(self):
        if not self.valid():
            raise PlanError(
                "agent_scope_expired", "The managed Agent authority is no longer active", status=403
            )


def current_scope():
    scope = _current.get()
    if scope is not None:
        scope.require()
    return scope


def owns_session(session):
    scope = current_scope()
    return bool(
        scope
        and scope.session_id == session.session_id
        and scope.run_id == getattr(session, "agent_run_id", None)
    )


class AgentGrants:
    def __init__(self, manager, *, clock=time.monotonic):
        self.manager, self.clock = manager, clock
        self._tokens = {}

    def issue(self, run_id, session_id, *, active=lambda: True, ttl=300):
        session = self.manager.get(session_id)
        if getattr(session, "agent_run_id", None) != run_id or session.manifest is None:
            raise PlanError(
                "agent_scope_unavailable", "The Session is not owned by this Agent run", status=409
            )
        revision, expires = session.manifest.sceneRevision, self.clock() + ttl
        token = PREFIX + secrets.token_urlsafe(32)

        def valid():
            return (
                self.clock() < expires
                and token in self._tokens
                and active()
                and session.state in ("RUNNING", "PAUSED")
                and session.manifest is not None
                and session.manifest.sceneRevision == revision
                and session.agent_run_id == run_id
            )

        self._tokens[token] = ManagedScope(
            run_id, session_id, revision, session.evaluation_id, "managed-" + run_id, valid
        )
        return token

    def revoke(self, run_id):
        self._tokens = {token: scope for token, scope in self._tokens.items() if scope.run_id != run_id}

    def authenticate(self, token):
        scope = self._tokens.get(token)
        if scope is None:
            raise PlanError("agent_scope_required", "A valid managed Agent grant is required", status=401)
        scope.require()
        return scope

    def permits(self, scope, method, path, query, body):
        base = "/api/v1/sessions/" + scope.session_id
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
        if path == "/api/v1/plans" and method == "POST":
            return (
                body.get("kind", "operation") == "operation"
                and body.get("target", "session") == "session"
                and body.get("sessionId") == scope.session_id
            )
        if path == "/api/v1/plans" and method == "GET":
            return query.getlist("sessionId") == [scope.session_id]
        if path == "/api/v1/inbox" and method == "GET":
            kinds = query.getlist("kinds")
            return (
                query.getlist("sessionId") == [scope.session_id]
                and len(kinds) == 1
                and set(kinds[0].split(",")) <= {"alert", "invocation", "plan"}
            )
        match = re.fullmatch(r"/api/v1/plans/(plan-[A-Za-z0-9-]+)(/revisions)?", path)
        if match:
            record = self.manager.plans.records.get(match[1])
            if (
                record is None
                or record.sessionId != scope.session_id
                or record.sceneRevision != scope.revision
            ):
                return False
            return (method == "GET" and not match[2]) or (
                method == "POST"
                and match[2]
                and body.get("sessionId") == scope.session_id
                and body.get("kind", "operation") == "operation"
                and body.get("target", "session") == "session"
            )
        if path == "/api/v1/alerts" and method == "GET":
            return query.getlist("sessionId") == [scope.session_id]
        match = re.fullmatch(r"/api/v1/alerts/([^/]+)/ack", path)
        if match and method == "POST":
            session = self.manager.get(scope.session_id)
            return bool(session.alerts and session.alerts.get(match[1]))
        return False


def install_agent_scopes(app, manager):
    grants = AgentGrants(manager)
    app.state.agent_grants = grants
    manager.agent_grants = grants

    @app.middleware("http")
    async def managed_authority(request, call_next):
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not token.startswith(PREFIX):
            return await call_next(request)
        reset = None
        try:
            scope = grants.authenticate(token)
            body = await request.json() if request.method in ("POST", "PUT", "PATCH") else {}
            scope.require()
            if not isinstance(body, dict) or not grants.permits(
                scope, request.method, request.url.path, request.query_params, body
            ):
                raise PlanError(
                    "agent_capability_denied", "This interface is not granted to Target", status=403
                )
            reset = _current.set(scope)
            return await call_next(request)
        except ValueError:
            return JSONResponse({"detail": {"code": "invalid_agent_request"}}, status_code=422)
        except PlanError as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status)
        finally:
            if reset is not None:
                _current.reset(reset)
