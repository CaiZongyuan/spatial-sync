"""Durable attention stream with authenticated cursors and fixed-prefix reads."""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import threading
from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import Field

from packages.info.journal import AppendJournal, JournalError
from packages.protocol.schema import JsonValue, Model

KINDS = frozenset(("alert", "approval", "invocation", "plan", "job"))
SCHEMA = "lw.inbox/1"


class InboxUnavailable(Exception):
    pass


class InvalidCursor(ValueError):
    pass


class InboxMessage(Model):
    seq: int = Field(strict=True, ge=1)
    kind: Literal["alert", "approval", "invocation", "plan", "job"]
    event: str
    sessionId: str | None = None
    data: dict[str, JsonValue]


def selected_kinds(value):
    if value is None:
        return KINDS
    selected = frozenset(value.split(","))
    if not selected or not selected <= KINDS:
        raise ValueError("kinds must be a comma-separated list of alert, approval, invocation, plan, job")
    return selected


class InboxStore:
    def __init__(self, path: Path):
        self.path = path
        self.journal = AppendJournal(path, label="Inbox")
        self._lock = threading.Lock()
        self._started = False
        self._failure = None
        self._header = None
        self._messages = []
        self._approvals = {}
        self._plans = {}
        self._jobs = {}
        self._tail = None

    def startup(self):
        with self._lock:
            if self._started:
                return
            try:
                if not self.path.exists():
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    header = {"schema": SCHEMA, "stream": secrets.token_hex(16), "key": secrets.token_hex(32)}
                    with self.path.open("x", encoding="utf-8", newline="\n") as stream:
                        stream.write(json.dumps(header) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                lines = self.path.read_bytes().splitlines(keepends=True)
                if not lines or not lines[0].endswith(b"\n"):
                    raise ValueError("Inbox stream identity is incomplete")
                header = json.loads(lines[0])
                if (header["schema"] != SCHEMA or len(bytes.fromhex(header["stream"])) != 16
                        or len(bytes.fromhex(header["key"])) != 32):
                    raise ValueError("Invalid inbox stream identity")
                self._header = header
                complete = len(lines[0])
                for line in lines[1:]:
                    if not line.endswith(b"\n"):
                        with self.path.open("r+b") as stream:
                            stream.truncate(complete)
                        break
                    message = InboxMessage.model_validate_json(line).model_dump(mode="json")
                    if message["seq"] != len(self._messages) + 1:
                        raise ValueError("Inbox sequence is not contiguous")
                    self._messages.append(message)
                    self._project(message)
                    complete += len(line)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._failure = str(error)
                logging.getLogger(__name__).warning("Inbox history unavailable at startup")
            self._started = True

    def _project(self, message):
        data = message["data"]
        if message["kind"] == "approval":
            self._approvals[data["approvalId"]] = data
        elif message["kind"] == "plan":
            self._plans[data["id"]] = data
        elif message["kind"] == "job":
            self._jobs[data["jobId"]] = data

    def _require_available(self):
        if not self._started or self._failure or self.journal.error:
            raise InboxUnavailable("Inbox history could not be saved or read")

    def _append(self, kind, event, data, session_id=None):
        """Caller owns _lock. Producer threads never touch an asyncio object."""
        self._require_available()
        message = InboxMessage(seq=len(self._messages) + 1, kind=kind, event=event,
                               sessionId=session_id, data=data)
        payload = message.model_dump_json()
        ticket = self.journal.append(payload)
        owned = json.loads(payload)
        self._messages.append(owned)
        self._project(owned)
        self._tail = ticket

    def _failed_publication(self, error):
        # Notification persistence never changes primary action/approval results.
        if self._failure is None:
            self._failure = str(error) or type(error).__name__
            logging.getLogger(__name__).warning("Inbox history publication unavailable")

    def approval(self, record):
        if record.sessionId and getattr(self, "is_restricted_session", lambda _: False)(record.sessionId):
            return
        with self._lock:
            try:
                self._require_available()
                data = record.model_dump(mode="json")
                previous = self._approvals.get(record.approvalId)
                if previous is None or previous["status"] != record.status:
                    self._append("approval", record.status, data, record.sessionId)
                else:
                    self._approvals[record.approvalId] = data
            except Exception as error:
                self._failed_publication(error)

    def plan(self, record):
        if record.sessionId and getattr(self, "is_restricted_session", lambda _: False)(record.sessionId):
            return
        with self._lock:
            try:
                self._require_available()
                data = record.plan.model_dump(mode="json") | {
                    "sessionId": record.sessionId,
                    "sceneRevision": record.sceneRevision, "epoch": record.epoch,
                }
                if self._plans.get(record.plan.id) != data:
                    self._append("plan", record.plan.status, data, record.sessionId)
            except Exception as error:
                self._failed_publication(error)

    def session_message(self, session_id, message):
        if getattr(self, "is_restricted_session", lambda _: False)(session_id):
            return
        kind = getattr(message, "type", None)
        if kind == "entity.alert" and message.state in ("raised", "cleared"):
            selected, event = "alert", message.state
        elif kind == "invocation.update" and message.status in ("failed", "rejected"):
            selected, event = "invocation", message.status
        else:
            return
        with self._lock:
            try:
                self._append(selected, event, message.model_dump(mode="json"), session_id)
            except Exception as error:
                self._failed_publication(error)

    def job(self, record):
        with self._lock:
            try:
                data = {key: record[key] for key in (
                    "jobId", "kind", "status", "planId", "planRevision", "task", "seed", "reason",
                )}
                if self._jobs.get(record["jobId"]) != data:
                    self._append("job", record["status"], data)
            except Exception as error:
                self._failed_publication(error)

    def reconcile(self, approvals, plans):
        for record in approvals:
            self.approval(record)
        for record in plans:
            self.plan(record)

    def _cursor(self, sequence):
        payload = struct.pack(">Q", sequence)
        signature = hmac.digest(bytes.fromhex(self._header["key"]),
                                self._header["stream"].encode() + payload, hashlib.sha256)
        return "inbox1_" + base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def _position(self, cursor):
        try:
            if not cursor.startswith("inbox1_"):
                raise ValueError("Unknown cursor")
            raw = base64.b64decode(cursor[7:] + "=" * (-len(cursor[7:]) % 4), altchars=b"-_", validate=True)
            if len(raw) != 40:
                raise ValueError("Invalid cursor length")
            sequence = struct.unpack(">Q", raw[:8])[0]
            if not hmac.compare_digest(cursor, self._cursor(sequence)) or sequence > len(self._messages):
                raise ValueError("Cursor is invalid for this inbox stream")
            return sequence
        except (ValueError, TypeError, struct.error) as error:
            raise InvalidCursor(
                "Cursor is invalid for this inbox stream; omit after for a fresh snapshot"
            ) from error

    async def poll(self, after, kinds, alerts):
        with self._lock:
            self._require_available()
            position = self._position(after) if after is not None else None
            sequence = len(self._messages)
            # Sequences are contiguous, so cursor positions index the next item.
            messages = [message for message in self._messages[position or 0:] if message["kind"] in kinds]
            response = {
                "messages": messages[-20:] if position is None else messages,
                "alerts": alerts if position is None and "alert" in kinds else [],
                "approvals": [record for record in self._approvals.values() if record["status"] == "pending"]
                if position is None and "approval" in kinds else [],
                "cursor": self._cursor(sequence),
            }
            response = deepcopy(response)
            ticket = self._tail
        try:
            await self.journal.wait(ticket)
        except JournalError as error:
            raise InboxUnavailable("Inbox history could not be saved or read") from error
        return response

    async def flush(self):
        try:
            await self.journal.flush()
        except JournalError:
            pass  # Polls expose the sticky failure; gateway lifecycle still proceeds.

    async def close(self):
        try:
            await self.journal.close()
        except JournalError:
            pass
