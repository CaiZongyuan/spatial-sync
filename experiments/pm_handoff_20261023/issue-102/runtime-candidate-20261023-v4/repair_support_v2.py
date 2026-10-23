"""SOURCE-ONLY v2. Whole observer calls are non-masking; not executed yet."""
import copy
import json
from pathlib import Path
import time

MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


def note_invalid(recorder, message):
    try:
        recorder.invalid(message)
    except BaseException:
        pass  # an observer failure must not replace the original application error


def guard_export(recorder, callback):
    try:
        return callback()
    except BaseException as error:
        note_invalid(recorder, "server export observer failed: " + type(error).__name__)
        return False


def atomic_bytes(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


class DrainSnapshot:
    def __init__(self, output, recorder):
        self.output, self.recorder = output, recorder
        self.payload = None
        self.capture_failure = None

    def observe(self, *args):
        try:
            self.capture(*args)
        except BaseException as error:
            self.capture_failure = type(error).__name__
            note_invalid(self.recorder, "drain capture failed: " + self.capture_failure)

    def capture(self, final, readers, accepted_ids, cursor, repeats, wakes, windows):
        basic = {"diagnosticStatus": "partial-unvalidated", "correctnessQualified": False,
                 "capturedAtMs": time.monotonic() * 1000,
                 "heldFinalQueueDepths": [item["reliableQueueDepth"] for item in final["clients"]]}
        if len(readers) != 2 or len(accepted_ids) > 4096 or any(len(reader.values) > 1000 or len(reader.revisions) > 1000 for reader in readers):
            self.payload = json.dumps(basic | {"diagnosticStatus": "invalid", "captureBoundsExceeded": True}).encode()
            note_invalid(self.recorder, "drain capture bounds exceeded")
            return
        state = [{"error": reader.error, "changes": reader.changes, "batches": reader.batches,
                  "digest": reader.digest.hexdigest(), "infoMessages": reader.info_messages,
                  "infoDigest": reader.info_digest.hexdigest(), "values": reader.values,
                  "revisions": reader.revisions,
                  "acceptedInvocations": {key: reader.invocations.get(key) for key in sorted(accepted_ids)}}
                 for reader in readers]
        data = basic | {"heldFinalStats": final, "readerStateScope": "last full aggregate, not a fresh Source query",
                        "lastAggregateCall": getattr(readers, "calls", [])[-1:] or None,
                        "readers": state, "acceptedIds": sorted(accepted_ids), "historyCursor": cursor,
                        "phaseMetricsUnqualified": repeats, "wakeSamplesUnqualified": wakes,
                        "measuredWindows": windows}
        encoded = json.dumps(copy.deepcopy(data), allow_nan=False, separators=(",", ":")).encode()
        self.payload = encoded if len(encoded) <= MAX_SNAPSHOT_BYTES else json.dumps(basic | {"diagnosticStatus": "invalid", "captureBoundsExceeded": True}).encode()
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            note_invalid(self.recorder, "drain capture byte bound exceeded")

    def persist_on_failure(self, error):
        if self.output is None:
            return
        data = json.loads(self.payload) if self.payload is not None else {"diagnosticStatus": "invalid" if self.capture_failure else "partial-unvalidated", "captureNotReached": True, "correctnessQualified": False}
        data.update(failureCategory=type(error).__name__, captureFailureCategory=self.capture_failure)
        atomic_bytes(Path(self.output) / "correctness-failure-snapshot.json", json.dumps(data, allow_nan=False, separators=(",", ":")).encode())


async def preserve_benchmark_failure(benchmark, snapshot, *args, **kwargs):
    try:
        return await benchmark(*args, **kwargs)
    except BaseException as original:
        try:
            snapshot.persist_on_failure(original)
        except BaseException as export_error:
            try:
                original.add_note("correctness snapshot export failed: " + type(export_error).__name__)
            except BaseException:
                pass
        raise  # the original AssertionError/cancellation object, never an observer error


def dump_server_trace(path_factory, recorder, monitor_stopped, identity_factory, phase, lifecycle_error):
    # Entire assembly, including identity/path.exists/document/JSON, is guarded.
    try:
        path = Path(path_factory())
        if phase == "outer-finally-fallback" and path.exists():
            return True
        identity = identity_factory()
        data = recorder.document() | {"monitorStopped": monitor_stopped, "executionIdentity": identity,
                                      "exportPhase": phase, "lifecycleFinished": phase == "lifespan-finally",
                                      "lifecycleErrorCategory": lifecycle_error,
                                      "diagnosticStatus": "invalid" if lifecycle_error or not monitor_stopped or recorder.dropped or recorder.problems else "partial-unvalidated"}
        atomic_bytes(path, json.dumps(data, allow_nan=False, separators=(",", ":")).encode())
        return True
    except BaseException as error:
        note_invalid(recorder, "server trace export failed: " + type(error).__name__)
        return False


def require_server_trace(server):
    if (server.get("diagnosticStatus") != "partial-unvalidated" or server.get("lifecycleErrorCategory")
            or server.get("lifecycleFinished") is not True or server.get("exportPhase") != "lifespan-finally"
            or server.get("dropped") or server.get("problems") or server.get("monitorStopped") is not True):
        raise RuntimeError("server lifecycle/export trace is invalid or incomplete")
