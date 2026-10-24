"""Gateway approval metadata and append-only audit, separate from spatial recordings."""

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.protocol.schema import Actor

Subject = Literal["invocation", "plan", "policy", "job", "deletion"]
Decision = Literal["approve", "deny", "revoke"]


class ApprovalConflict(Exception):
    pass


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Decision
    actor: Actor
    atMs: float
    note: str = ""


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approvalId: str
    subject: Subject
    subjectId: str
    sessionId: str | None = None
    title: str
    details: dict[str, Any] = Field(default_factory=dict)
    requestedBy: Actor
    createdAtMs: float
    status: Literal["pending", "approved", "denied", "revoked", "expired", "unavailable"] = "pending"
    decisions: list[ApprovalDecision] = Field(default_factory=list)


class ApprovalStore:
    def __init__(self, path: Path, *, on_change=None):
        self.path = path
        self.records: dict[str, ApprovalRecord] = {}
        self._invocations = {}
        self._started = False
        self._write_lock = threading.Lock()
        self.on_change = on_change

    def startup(self):
        if self._started:
            return
        if self.path.exists():
            lines = self.path.read_bytes().splitlines(keepends=True)
            complete = 0
            for line in lines:
                if not line.endswith(b"\n"):
                    # A killed append cannot turn a partial final record into a decision.
                    with self.path.open("r+b") as stream:
                        stream.truncate(complete)
                    break
                record = ApprovalRecord.model_validate_json(line)
                self.records[record.approvalId] = record
                if record.subject == "invocation":
                    self._invocations[record.subjectId] = record.approvalId
                complete += len(line)
        # Gateway-owned sessions do not survive restart. Keep the audit, close stale requests.
        for record in list(self.records.values()):
            if record.status == "pending" and record.subject == "invocation":
                self.set_status(record.approvalId, "unavailable")
        self._started = True

    def _save(self, record):
        # The action pipeline and library worker can append to the same audit concurrently.
        with self._write_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(payload + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.records[record.approvalId] = record
            if record.subject == "invocation":
                self._invocations[record.subjectId] = record.approvalId
            if self.on_change is not None:
                self.on_change(record)
        return record

    def submit(self, *, subject: Subject, subject_id, requester, title, details=None, session_id=None):
        """The same subject envelope is available to later plan/policy/job/deletion consumers."""
        return self._save(
            ApprovalRecord(
                approvalId="approval-" + uuid.uuid4().hex,
                subject=subject,
                subjectId=subject_id,
                sessionId=session_id,
                title=title,
                details=details or {},
                requestedBy=requester,
                createdAtMs=time.time() * 1000,
            )
        )

    def for_invocation(self, invocation_id):
        approval_id = self._invocations.get(invocation_id)
        return self.records.get(approval_id)

    def first_decision(self, approval_ids, decision):
        """Return real durable audit order, independent of IDs and wall-clock ordering."""
        identities = set(approval_ids)
        if not identities:
            return None
        with self._write_lock:
            payload = self.path.read_bytes() if self.path.exists() else b""
        for line in payload.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break  # A partial append never establishes an approval decision.
            value = json.loads(line)
            if value["approvalId"] not in identities:
                continue
            record = ApprovalRecord.model_validate(value)
            event = next((event for event in record.decisions if event.decision == decision), None)
            if event is not None:
                return record.approvalId, event
        return None

    def decide(self, approval_id, decision: Decision, actor, note=""):
        record = self.records[approval_id]
        status = {"approve": "approved", "deny": "denied", "revoke": "revoked"}[decision]
        return self._save(
            record.model_copy(
                update={
                    "status": status,
                    "decisions": [
                        *record.decisions,
                        ApprovalDecision(
                            decision=decision,
                            actor=actor,
                            note=note,
                            atMs=time.time() * 1000,
                        ),
                    ],
                }
            )
        )

    def set_status(self, approval_id, status):
        record = self.records[approval_id]
        return self._save(record.model_copy(update={"status": status}))
