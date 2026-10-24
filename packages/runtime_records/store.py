"""One durable imported-record store, with no runtime or filesystem-launch authority."""

import json
import threading
from copy import deepcopy
from datetime import UTC, datetime

from pydantic import Field, JsonValue, model_validator

from packages.info.plans import PlanError
from packages.library.transactions import durable_json
from packages.runtime_records.native import digest, merge_metric, normalize, redacted, usage_summary
from packages.training.dispatch import training_store, training_stores
from packages.training.schema import Schema

MAX_BYTES = 16 * 1024 * 1024


class Associations(Schema):
    agentId: str | None = Field(default=None, max_length=256)
    harnessVersion: int | None = Field(default=None, ge=1)
    planId: str | None = Field(default=None, max_length=256)
    planRevision: int | None = Field(default=None, ge=1)
    goalId: str | None = Field(default=None, max_length=256)


class ImportCapture(Schema):
    format: str = Field(min_length=1, max_length=80)
    content: str | None = None
    records: list[dict[str, JsonValue]] | None = Field(default=None, max_length=50000)
    sourceLabel: str | None = Field(default=None, max_length=256)
    associations: Associations | None = None

    @model_validator(mode="after")
    def one_input(self):
        if (self.content is None) == (self.records is None):
            raise ValueError("Supply exactly one native content or records input")
        return self


class RuntimeRecords:
    def __init__(self, manager, agents):
        self.manager, self.agents = manager, agents
        self.root = manager.settings.state_root.resolve()
        self.path = self.root / "runtime-sessions.json"
        self.records = {}
        self._lock = threading.RLock()

    def startup(self):
        if self.path.exists():
            if self.path.is_symlink() or self.path.is_junction() or self.path.stat().st_nlink != 1:
                raise PlanError(
                    "runtime_store_unavailable",
                    "Runtime record store is not a regular owned file",
                    status=503,
                )
            self.records = json.loads(self.path.read_bytes())["sessions"]

    def references(self, body):
        if body is None:
            return None
        values = body.model_dump(exclude_none=True)
        if body.harnessVersion is not None and body.agentId is None:
            raise PlanError(
                "invalid_runtime_association", "Harness version requires an explicit Agent", status=422
            )
        if body.agentId is not None:
            values["agentId"] = self.agents.show(body.agentId)["id"]
        if body.harnessVersion is not None:
            self.agents.version(body.agentId, body.harnessVersion)
        if (body.planId is None) != (body.planRevision is None):
            raise PlanError("invalid_runtime_association", "Supply both Plan ID and revision", status=422)
        if body.planId is not None:
            if any(body.planId in selected.records for selected in training_stores(self.manager)):
                training_store(self.manager, identifier=body.planId).get(body.planId, body.planRevision)
            else:
                self.manager.plans.get(body.planId, body.planRevision)
        if body.goalId is not None and body.goalId not in self.manager.notebooks.goals():
            raise PlanError("invalid_runtime_association", "Select an existing Goal", status=422)
        return values

    def ingest(self, body, *, private_context=None):
        native = body.records
        if body.content is not None:
            if len(body.content.encode()) > MAX_BYTES:
                raise PlanError("runtime_capture_too_large", "Import captures up to16MiB", status=413)
            try:
                native = [json.loads(line) for line in body.content.splitlines() if line.strip()]
            except (ValueError, RecursionError) as error:
                raise PlanError(
                    "invalid_runtime_jsonl",
                    "A complete JSON object is required on each native line",
                    status=422,
                ) from error
        try:
            serialized = json.dumps(native, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            raise PlanError(
                "invalid_runtime_record", "Native capture contains invalid JSON values", status=422
            ) from error
        if len(serialized) > MAX_BYTES:
            raise PlanError("runtime_capture_too_large", "Import captures up to16MiB", status=413)
        parsed = normalize(body.format, native)
        rsi = getattr(self.manager, "agent_rsi", None)
        bound = rsi.private_native_context(parsed["nativeSessionId"]) if rsi is not None else None
        if private_context is None and bound is not None:
            raise PlanError(
                "runtime_capture_restricted", "Use the owning promotion capture intake", status=409
            )
        if private_context is not None and private_context != bound:
            raise PlanError(
                "runtime_capture_scope_conflict", "Capture is not bound to this private case", status=409
            )
        references = self.references(body.associations)
        identity = (
            "runtime-session-"
            + digest({key: parsed[key] for key in ("runtime", "nativeSessionId", "captureId")})[:24]
        )
        observed = datetime.now(UTC).isoformat()
        with self._lock:
            for other in self.records.values():
                if other["nativeSessionId"] == parsed["nativeSessionId"] and other["id"] != identity:
                    raise PlanError(
                        "runtime_capture_scope_conflict",
                        "This native session already has a different capture scope",
                        status=409,
                    )
            prior = self.records.get(identity)
            if prior and prior.get("visibility") == "private" and private_context is None:
                raise PlanError(
                    "runtime_capture_restricted", "Capture has restricted evaluation scope", status=409
                )
            record = (
                deepcopy(prior)
                if prior
                else {
                    "id": identity,
                    "source": "runtime-import",
                    "runtime": parsed["runtime"],
                    "format": parsed["format"],
                    "nativeSessionId": parsed["nativeSessionId"],
                    "captureId": parsed["captureId"],
                    "runtimeVersion": parsed["runtimeVersion"],
                    "importedAt": observed,
                    "associations": {},
                    "events": [],
                    "receipts": {},
                }
            )
            if private_context is not None:
                record.update(visibility="private", privateContext=deepcopy(private_context))
            if record["format"] != parsed["format"]:
                raise PlanError(
                    "runtime_capture_scope_conflict",
                    "Use the original native format for this capture",
                    status=409,
                )
            indexed = {event["id"]: event for event in record["events"]}
            prior_results = {event["id"] for event in record["events"] if event["nativeType"] == "result"}
            incoming_results = {event["id"] for event in parsed["events"] if event["nativeType"] == "result"}
            backfill = bool(prior_results) and (
                any(
                    event["id"] not in indexed
                    and (event["nativeType"] in {"assistant", "user"} or event["kind"] in {"message", "tool"})
                    for event in parsed["events"]
                )
                or any(
                    key.startswith("message:")
                    and merge_metric(record["receipts"].get(key), value) != record["receipts"].get(key)
                    for key, value in parsed["receipts"].items()
                )
            )
            # Only a complete native snapshot establishes that backfilled conversation
            # belongs to an already retained result; partial increments cannot extend it.
            if prior_results and (
                incoming_results - prior_results
                or backfill
                and (
                    incoming_results != prior_results
                    or not indexed.keys() <= {event["id"] for event in parsed["events"]}
                    or any(
                        merge_metric(value, parsed["receipts"].get(key, {})) != parsed["receipts"].get(key)
                        for key, value in record["receipts"].items()
                        if key.startswith("capture:")
                    )
                )
            ):
                raise PlanError(
                    "unsupported_runtime_counter_scope",
                    "Refresh a completed capture with its full ordered snapshot and original result",
                    status=409,
                )
            added = 0
            for event in parsed["events"]:
                previous = indexed.get(event["id"])
                if previous is not None and previous["contentHash"] != event["contentHash"]:
                    raise PlanError(
                        "runtime_event_conflict", "An existing native event changed its content", status=409
                    )
                if previous is None:
                    indexed[event["id"]] = event
                    record["events"].append(event)
                    added += 1
            if backfill:
                record["events"] = [
                    indexed[key] for key in dict.fromkeys(event["id"] for event in parsed["events"])
                ]
            updated = 0
            for key, metric in parsed["receipts"].items():
                before = record["receipts"].get(key)
                after = merge_metric(before, metric)
                if before != after:
                    updated += int(before is not None)
                    record["receipts"][key] = after
            if references is not None:
                record["associations"] = references
            record["status"] = (
                parsed["status"] if parsed["status"] != "observed" else record.get("status", "observed")
            )
            record["sourceLabel"] = (
                redacted(body.sourceLabel) if body.sourceLabel is not None else record.get("sourceLabel")
            )
            record["usage"] = usage_summary(record["receipts"])
            times = [event["timestamp"] for event in record["events"] if event["timestamp"] is not None]
            record.update(
                createdAt=min(times) if times else None,
                updatedAt=max(times) if times else None,
                eventCount=len(record["events"]),
                messageCount=len(
                    {
                        (event["role"], event["messageId"] or event["id"])
                        for event in record["events"]
                        if event["role"] in {"assistant", "user"}
                    }
                ),
                toolCallCount=len(
                    {
                        block["id"]
                        for event in record["events"]
                        if isinstance(event["content"], list)
                        for block in event["content"]
                        if block.get("type") == "tool_use" and block.get("id")
                    }
                ),
                models=sorted(set(record.get("models", [])) | set(parsed["models"])),
                thinking=None,
            )
            record["contentHash"] = digest(
                {
                    "events": record["events"],
                    "receipts": record["receipts"],
                    "associations": record["associations"],
                }
            )
            identical = prior is not None and all(
                prior.get(key) == value for key, value in record.items() if key != "lastImportedAt"
            )
            if not identical:
                record["lastImportedAt"] = observed
                records = self.records | {identity: record}
                try:
                    durable_json(self.path, {"version": 1, "sessions": records})
                except OSError as error:
                    raise PlanError(
                        "runtime_store_unavailable", "Imported runtime records could not be saved", status=503
                    ) from error
                self.records = records
            return {
                "session": self.document(record, private=private_context is not None),
                "addedEvents": added,
                "updatedEvents": updated,
                "idempotent": identical,
            }

    def get(self, identity):
        record = self.records.get(identity)
        if record is None:
            raise PlanError("unknown_runtime_session", "Unknown imported runtime session", status=404)
        return record

    def artifact(self, record, identity, *, private=False):
        if record.get("visibility") == "private" and not private:
            raise PlanError("unknown_runtime_session", "Unknown imported runtime session", status=404)
        if identity == "transcript":
            return (
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in record["events"])
            ).encode(), "application/x-ndjson"
        if identity == "usage":
            return json.dumps(
                {"source": "runtime-import", "summary": record["usage"], "receipts": record["receipts"]},
                ensure_ascii=False,
            ).encode(), "application/json"
        raise PlanError("unknown_runtime_artifact", "Unknown normalized runtime evidence", status=404)

    def document(self, record, *, detail=False, private=False):
        import hashlib

        if record.get("visibility") == "private" and not private:
            raise PlanError("unknown_runtime_session", "Unknown imported runtime session", status=404)
        result = deepcopy({key: value for key, value in record.items() if key not in {"events", "receipts"}})
        rsi = getattr(self.manager, "agent_rsi", None)
        execution = rsi.native_associations(record["id"]) if rsi is not None else {}
        if execution:
            result["executionAssociations"] = execution
        result["artifacts"] = []
        for identity, kind in (("transcript", "runtime-transcript"), ("usage", "runtime-usage")):
            content, _ = self.artifact(record, identity, private=private)
            result["artifacts"].append(
                {
                    "id": identity,
                    "kind": kind,
                    "source": "runtime-import",
                    "url": f"/api/v1/runtime-sessions/{record['id']}/artifacts/{identity}",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        if detail:
            result["events"] = deepcopy(record["events"])
        return result

    def mark_private(self, native_session_id, context):
        """Trusted case binding also restricts an earlier explicit capture of that native conversation."""
        with self._lock:
            records = dict(self.records)
            for identity, record in records.items():
                if record["nativeSessionId"] == native_session_id:
                    records[identity] = record | {
                        "visibility": "private", "privateContext": deepcopy(context)
                    }
            durable_json(self.path, {"version": 1, "sessions": records})
            self.records = records
