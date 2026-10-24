"""Issue role-bound capabilities, without handing runtime or human credentials to a model."""

import json
import os
import time
from datetime import UTC, datetime, timedelta

from packages.agent_rsi.scope import WorkflowGrant
from packages.info.plans import PlanError
from packages.runtime_records.native import redacted


def connection(grant):
    return {
        "token": grant.token,
        "tokenType": "Bearer",
        "principal": grant.kind,
        "agentId": grant.agent_id,
        "goalId": grant.goal_id,
        "promotionId": grant.promotion_id,
        "expiresAt": (
            datetime.now(UTC) + timedelta(seconds=max(0, grant.deadline - time.monotonic()))
        ).isoformat(),
    }


def issue_optimizer(service, identifier, body):
    with service._lock:
        if service.audit_error:
            raise PlanError("agent_trace_unavailable", "Workflow Trace is unavailable", status=503)
        agent = service.agents.show(identifier)
        known = service.optimizer_sessions.get(body.nativeSessionId)
        if known is not None:
            if (known["agentId"], known["goalId"], known["requestId"], known["referenceEvaluationId"]) != (
                agent["id"],
                body.goalId,
                body.requestId,
                body.referenceEvaluationId,
            ):
                raise PlanError("native_session_reused", "Use a fresh Optimizer conversation", status=409)
            active = next(
                (
                    grant
                    for grant in service.grants.values()
                    if grant.native_session_id == body.nativeSessionId and not grant.revoked
                ),
                None,
            )
            if active is not None:
                active.require()
                return connection(active)
            raise PlanError("native_session_reused", "Restarted work requires a fresh Optimizer", status=409)
        reference = next(
            (
                value
                for value in service.baselines.values()
                if value["agentId"] == agent["id"] and value["evaluationId"] == body.referenceEvaluationId
            ),
            None,
        )
        if reference is None or reference["harnessVersion"] != agent["referenceVersion"]:
            raise PlanError("agent_baseline_required", "Select a measured current Reference", status=409)
        if service.private_native_context(body.nativeSessionId) is not None or any(
            case.get("nativeSessionId") == body.nativeSessionId
            for run in service.manager.evaluations.store.records.values()
            if run.get("layer") == "agent"
            for case in run["cases"]
        ):
            raise PlanError("native_session_reused", "Use a fresh Optimizer conversation", status=409)
        grant = WorkflowGrant(
            service,
            "optimizer",
            agent["id"],
            body.goalId,
            time.monotonic() + body.wallSeconds,
            native_session_id=body.nativeSessionId,
            reference_evaluation_id=body.referenceEvaluationId,
            reference_version=reference["harnessVersion"],
        )
        context = {
            "agentId": agent["id"],
            "goalId": body.goalId,
            "requestId": body.requestId,
            "referenceEvaluationId": body.referenceEvaluationId,
            "contaminated": False,
        }
        service._write(optimizer_sessions=service.optimizer_sessions | {body.nativeSessionId: context})
        service.grants[grant.token] = grant
        return connection(grant)


def record_tool(service, grant, method, path, status, *, request_id=None):
    # Public trace keeps the call's interface/status, not private payloads, identifiers or credentials.
    value = redacted(
        {
            "at": datetime.now(UTC).isoformat(),
            "principal": grant.kind,
            "principalId": grant.identifier,
            "agentId": grant.agent_id,
            "goalId": grant.goal_id,
            "method": method if method in ("GET", "POST", "PUT", "PATCH", "DELETE") else "OTHER",
            "interface": "/".join(path.split("/")[:4]),
            "requestId": request_id,
            "phase": "begin" if status is None else "end",
            "status": status,
        }
    )
    try:
        with service._audit_lock:
            file = service.manager.settings.state_root / "agent-rsi-tools.jsonl"
            if file.is_symlink() or file.is_junction() or file.exists() and file.stat().st_nlink != 1:
                raise OSError("Workflow Trace is not a regular file")
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    except (OSError, ValueError) as error:
        service.audit_error = True
        raise PlanError("agent_trace_unavailable", "Workflow Trace is unavailable", status=503) from error


def tool_trace(service, agent_id, goal):
    if service.audit_error:
        raise PlanError("agent_trace_unavailable", "Workflow Trace is unavailable", status=503)
    path = service.manager.settings.state_root / "agent-rsi-tools.jsonl"
    if not path.exists():
        return []
    with service._audit_lock:
        try:
            if path.is_symlink() or path.is_junction() or path.stat().st_nlink != 1:
                raise OSError("Workflow Trace is not a regular file")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError, UnicodeError) as error:
            service.audit_error = True
            raise PlanError("agent_trace_unavailable", "Workflow Trace is unavailable", status=503) from error
    return [
        row
        for row in rows
        if row["agentId"] == agent_id and row["goalId"] == goal and row["principal"] == "optimizer"
    ][-1000:]


def recover_trace(service):
    path = service.manager.settings.state_root / "agent-rsi-tools.jsonl"
    if not path.exists():
        return
    try:
        with service._audit_lock:
            if path.is_symlink() or path.is_junction() or path.stat().st_nlink != 1:
                raise OSError("Workflow Trace is not a regular file")
            offset = 0
            for line in path.read_bytes().splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    # An interrupted append proves no output or completed call.
                    with path.open("r+b") as stream:
                        stream.truncate(offset)
                    break
                row = json.loads(line)
                if (
                    not isinstance(row, dict)
                    or row.get("principal") not in ("optimizer", "promotion-controller")
                    or row.get("phase") not in ("begin", "end")
                    or not all(
                        isinstance(row.get(key), str) for key in ("at", "agentId", "goalId", "principalId")
                    )
                ):
                    raise ValueError("Invalid Workflow Trace")
                offset += len(line)
    except (OSError, ValueError, UnicodeError):
        service.audit_error = True
