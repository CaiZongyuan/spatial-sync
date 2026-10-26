"""Local maintenance supervisor; only this trusted host opens the metadata DB."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from packages.catalog_metadata import MetadataError
from packages.catalog_metadata.worker import (
    SKILLS_VERSION,
    maintenance_skill,
    normalize_profile,
    profile_from_environment,
    public_profile,
)

ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "partial", "failed", "cancelled", "interrupted", "budget_exhausted")
DEFAULT_BUDGETS = {
    "requests": 10000,
    "networkBytes": 268435456,
    "metadataBytes": 2097152,
    "tableBytes": 33554432,
    "previewBytes": 0,
    "modelTokens": 100000,
    "seconds": 1800,
    "timeout": 20,
    "attempts": 3,
}
BUDGET_MAXIMUMS = {**DEFAULT_BUDGETS, "previewBytes": 16777216}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _error(code, message, status=400):
    return MetadataError(code, message, status=status)


def validate_budgets(value=None):
    result = dict(DEFAULT_BUDGETS)
    for key, amount in (value or {}).items():
        maximum = BUDGET_MAXIMUMS.get(key)
        if maximum is None or isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise _error("invalid_maintenance_budget", "Select known numeric maintenance budgets")
        if not 0 <= amount <= maximum or (amount == 0 and key not in ("previewBytes", "modelTokens")):
            raise _error("invalid_maintenance_budget", "Maintenance budgets exceed their local limits")
        if key not in ("seconds", "timeout") and not isinstance(amount, int):
            raise _error("invalid_maintenance_budget", "Byte, request and token budgets must be integers")
        result[key] = amount
    return result


def _tokens(document):
    usage = document.get("usage")
    if usage is None:
        return None
    if not isinstance(usage, dict):
        raise _error("invalid_model_usage", "Reported usage must be an object or unknown")
    for key in ("inputTokens", "outputTokens", "totalTokens"):
        amount = usage.get(key)
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int) or amount < 0):
            raise _error(
                "invalid_model_usage", "Reported token usage must be a nonnegative integer or unknown"
            )
    if usage.get("totalTokens") is not None:
        return usage["totalTokens"]
    if all(usage.get(key) is not None for key in ("inputTokens", "outputTokens")):
        return usage["inputTokens"] + usage["outputTokens"]
    return None


class MetadataMaintenance:
    def __init__(
        self, store, *, agent_profile=None, launch_workers=True, index_builder=None, release_preparer=None
    ):
        self.store = store
        self.root = Path(store.root) / "maintenance"
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile = (
            profile_from_environment() if agent_profile is None else normalize_profile(agent_profile)
        )
        self.runtime = public_profile(self.profile)
        self.skills_digest = hashlib.sha256(maintenance_skill().encode("utf-8")).hexdigest()
        self.launch_workers = launch_workers
        self.index_builder = index_builder
        self.release_preparer = release_preparer
        self._lock = threading.RLock()
        self._processes = {}
        self._supervisors = {}
        self._worker_slots = asyncio.Semaphore(2)
        self._scheduler = None
        self._closed = False
        self._callback_url = os.environ.get("LW_CATALOG_WORKER_URL")

    async def startup(self):
        with self.store.transaction() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS maintenance_runs (
                    id TEXT PRIMARY KEY, collection_id TEXT NOT NULL, status TEXT NOT NULL,
                    token_hash TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS maintenance_active_collection
                    ON maintenance_runs(collection_id) WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS maintenance_items (
                    run_id TEXT NOT NULL, upstream_id TEXT NOT NULL, position INTEGER NOT NULL,
                    document TEXT NOT NULL, PRIMARY KEY(run_id,upstream_id), UNIQUE(run_id,position)
                );
                CREATE TABLE IF NOT EXISTS maintenance_tasks (
                    run_id TEXT NOT NULL, upstream_id TEXT NOT NULL, state TEXT NOT NULL,
                    document TEXT NOT NULL, PRIMARY KEY(run_id,upstream_id)
                );
                CREATE TABLE IF NOT EXISTS maintenance_extracted (
                    run_id TEXT NOT NULL, upstream_id TEXT NOT NULL,
                    input_digest TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(run_id,upstream_id)
                );
                CREATE TABLE IF NOT EXISTS maintenance_batches (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, state TEXT NOT NULL,
                    document TEXT NOT NULL
                );
            """)
            recovering = connection.execute(
                "SELECT run_id,id,document FROM maintenance_batches WHERE state='submitting'"
            ).fetchall()
        for row in recovering:
            batch = json.loads(row[2])
            try:
                self.submit(row[0], row[1], batch["receipt"])
            except MetadataError:
                # The retained receipt still records the actual call. A stale
                # result remains recoverable as a conflict, never zero usage.
                pass
        with self.store.transaction() as connection:
            active = connection.execute(
                "SELECT document FROM maintenance_runs WHERE status IN ('queued','running')"
            )
            for row in active.fetchall():
                record = json.loads(row[0])
                record.update(
                    status="interrupted",
                    phase="interrupted",
                    finishedAt=time.time(),
                    message="Gateway restarted; resume from the durable checkpoint",
                )
                self._save(connection, record, token_hash="")
        self._scheduler = asyncio.create_task(self._schedule(), name="catalog-maintenance-schedule")

    def _load(self, connection, identifier):
        row = connection.execute("SELECT document FROM maintenance_runs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise _error("unknown_maintenance_run", "Unknown maintenance run", 404)
        return json.loads(row[0])

    def _save(self, connection, record, *, token_hash=None):
        record["updatedAt"] = time.time()
        if token_hash is None:
            connection.execute(
                "UPDATE maintenance_runs SET status=?,document=? WHERE id=?",
                (record["status"], _json(record), record["id"]),
            )
        else:
            connection.execute(
                "UPDATE maintenance_runs SET status=?,document=?,token_hash=? WHERE id=?",
                (record["status"], _json(record), token_hash, record["id"]),
            )

    def show(self, identifier):
        with self.store.transaction() as connection:
            return self._load(connection, identifier)

    def status(self):
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT document FROM maintenance_runs ORDER BY rowid DESC LIMIT 20"
            ).fetchall()
        release = self.store.latest_release()
        return {
            "schema": SKILLS_VERSION,
            "runtime": self.runtime,
            "collections": self.store.collections(),
            "runs": [json.loads(row[0]) for row in rows],
            "release": release,
            "skillsVersion": SKILLS_VERSION,
        }

    def authorize_worker(self, path, token):
        prefix = "/api/v1/library/catalog/maintenance/runs/"
        if not path.startswith(prefix) or not token:
            return False
        identifier, _, suffix = path[len(prefix) :].partition("/")
        allowed = (
            not suffix
            or suffix
            in (
                "phase",
                "checkpoint",
                "inventory",
                "inventory/complete",
                "claim",
                "finish",
                "error",
                "extracted",
                "publish",
            )
            or suffix.startswith("evidence/")
            or (
                suffix.startswith("batches/")
                and suffix.rsplit("/", 1)[-1] in ("submit", "error", "execution")
            )
        )
        if not allowed:
            return False
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT token_hash,status FROM maintenance_runs WHERE id=?", (identifier,)
            ).fetchone()
        return bool(
            row
            and row[1] in ACTIVE
            and secrets.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest())
        )

    @staticmethod
    def callback_url(request):
        # Use the server socket, never an untrusted Host/Forwarded header.
        address = request.scope.get("server")
        if not address or address[0] == "testserver" or not isinstance(address[1], int):
            return None
        return "http://127.0.0.1:" + str(address[1])

    async def start(self, collection_id, *, revision=None, budgets=None, callback_url=None, request_id=None):
        collection = self.store.collection(collection_id)
        if not collection.get("enabled", False):
            raise _error("collection_disabled", "Enable the collection before maintenance", 409)
        selected = validate_budgets(budgets)
        vocabulary_version = self.store.vocabulary()["version"]
        token = "lw-meta-" + secrets.token_urlsafe(32)
        with self._lock, self.store.transaction() as connection:
            if request_id:
                rows = connection.execute(
                    "SELECT document FROM maintenance_runs WHERE collection_id=?", (collection_id,)
                )
                for row in rows:
                    previous = json.loads(row[0])
                    if previous.get("requestId") == request_id:
                        if previous["requestedRevision"] != revision or previous["budgets"] != selected:
                            raise _error(
                                "maintenance_request_conflict",
                                "Request ID was used with different inputs",
                                409,
                            )
                        return previous
            active = connection.execute(
                "SELECT id FROM maintenance_runs WHERE collection_id=? AND status IN ('queued','running')",
                (collection_id,),
            ).fetchone()
            if active:
                raise _error("maintenance_in_progress", "This collection already has an active run", 409)
            now = time.time()
            record = {
                "id": "meta-" + uuid.uuid4().hex,
                "collectionId": collection_id,
                "collection": collection,
                "requestedRevision": revision,
                "requestId": request_id,
                "status": "queued",
                "phase": "queued",
                "createdAt": now,
                "updatedAt": now,
                "finishedAt": None,
                "budgets": selected,
                "usage": {
                    "requests": 0,
                    "networkBytes": 0,
                    "previewBytes": 0,
                    "modelTokens": None,
                    "modelCalls": 0,
                    "seconds": 0,
                },
                "coverage": {"discovered": 0, "indexed": 0, "organized": 0, "failed": 0, "reused": 0},
                "inventoryId": None,
                "releaseId": None,
                "runtime": self.runtime,
                "versions": {
                    "skills": SKILLS_VERSION,
                    "skillsDigest": self.skills_digest,
                    "recipe": collection.get("recipeVersion", "1"),
                    "vocabulary": vocabulary_version,
                },
                "budgetExhausted": False,
                "message": "Queued for the local maintenance worker",
            }
            connection.execute(
                "INSERT INTO maintenance_runs VALUES(?,?,?,?,?)",
                (
                    record["id"],
                    collection_id,
                    "queued",
                    hashlib.sha256(token.encode()).hexdigest(),
                    _json(record),
                ),
            )
        self._launch(record, token, callback_url)
        return record

    def _launch(self, record, token, callback_url):
        selected = callback_url or self._callback_url
        if selected is not None:
            parsed = urlparse(selected)
            if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
                raise _error("invalid_worker_url", "Maintenance callbacks must use a loopback HTTP address")
            self._callback_url = selected
        if self.launch_workers and selected:
            task = asyncio.create_task(
                self._queued_worker(record["id"], token, selected), name="catalog-" + record["id"]
            )
            self._supervisors[record["id"]] = task

    async def _queued_worker(self, identifier, token, url):
        try:
            async with self._worker_slots:
                if self.show(identifier)["status"] in ACTIVE:
                    await self._supervise(identifier, token, url)
        finally:
            self._supervisors.pop(identifier, None)

    async def _supervise(self, identifier, token, url):
        env = os.environ.copy()
        env["LW_CATALOG_RUN_TOKEN"] = token
        if self.profile is not None:
            env["LW_CATALOG_AGENT_PROFILE"] = _json(self.profile)
        else:
            env.pop("LW_CATALOG_AGENT_PROFILE", None)
        cache_root = self.store.root / "blobs"
        log_root = self.root / identifier
        log_root.mkdir(parents=True, exist_ok=True)
        try:
            with (log_root / "worker.log").open("ab") as output:
                control_file = log_root / "cancel"
                control_file.unlink(missing_ok=True)
                process = await asyncio.create_subprocess_exec(
                    os.environ.get("LW_CATALOG_WORKER_PYTHON", sys.executable),
                    "-m",
                    "packages.catalog_metadata.worker",
                    "--url",
                    url,
                    "--run",
                    identifier,
                    "--cache-root",
                    str(cache_root),
                    "--control-file",
                    str(control_file),
                    cwd=Path(__file__).resolve().parents[2],
                    env=env,
                    stdout=output,
                    stderr=output,
                    creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
                )
                self._processes[identifier] = process
                try:
                    current = self.show(identifier)
                    remaining = max(0, current["budgets"]["seconds"] - current["usage"].get("seconds", 0))
                    await asyncio.wait_for(process.wait(), timeout=remaining + 10)
                except TimeoutError:
                    await self._stop_process(identifier)
                    self.fail(identifier, "runtime_limit", "Worker exceeded its runtime budget", budget=True)
                else:
                    if self.show(identifier)["status"] in ACTIVE:
                        self.fail(identifier, "worker_exited", "Worker exited before publishing its result")
        except asyncio.CancelledError:
            await self._stop_process(identifier)
            raise
        except OSError:
            self.fail(identifier, "worker_start_failed", "Unable to start the local metadata worker")
        finally:
            self._processes.pop(identifier, None)
            self._supervisors.pop(identifier, None)

    async def _stop_process(self, identifier):
        process = self._processes.get(identifier)
        if process is not None and process.returncode is None:
            (self.root / identifier / "cancel").write_text("cancel", encoding="utf-8")
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    def phase(self, identifier, phase):
        if phase not in ("enumerating", "extracting", "organizing", "checking", "publishing"):
            raise _error("invalid_maintenance_phase", "Unknown maintenance phase")
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            record.update(status="running", phase=phase)
            if phase == "enumerating" and record["usage"]["requests"] == 0:
                for key in ("requests", "networkBytes", "previewBytes"):
                    record["usage"][key] = None
            self._save(connection, record)
            return record

    def checkpoint(self, identifier, usage, *, source_revision=None):
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            if source_revision is not None:
                if not isinstance(source_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", source_revision):
                    raise _error("invalid_revision", "Source checkpoints require a fixed dataset revision")
                if record.get("sourceRevision") not in (None, source_revision):
                    raise _error(
                        "source_revision_conflict", "A maintenance run cannot change its source revision", 409
                    )
                record["sourceRevision"] = source_revision
            for key in ("requests", "networkBytes", "previewBytes", "embeddedPreviewBytes"):
                value = usage.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    if key == "embeddedPreviewBytes" and value is None:
                        continue
                    raise _error(
                        "invalid_maintenance_usage", "Checkpoint usage must contain actual nonnegative counts"
                    )
                record["usage"][key] = value
            record["sourceUsage"] = copy.deepcopy(usage)
            record["usage"]["seconds"] = time.time() - record["createdAt"]
            self._save(connection, record)
            return record

    @staticmethod
    def _active(record):
        if record["status"] not in ACTIVE:
            raise _error("maintenance_not_active", "Resume the checkpoint before submitting more work", 409)

    def ingest(self, identifier, entries, offset):
        if (
            not isinstance(entries, list)
            or not 1 <= len(entries) <= 24
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise _error(
                "invalid_inventory_batch", "Inventory batches contain at most 24 entries and an offset"
            )
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            if record["inventoryId"]:
                raise _error("inventory_closed", "The run's inventory is already closed", 409)
            for index, entry in enumerate(entries, offset):
                upstream = entry.get("upstreamId") if isinstance(entry, dict) else None
                if not isinstance(upstream, str) or not upstream:
                    raise _error(
                        "invalid_inventory_batch", "Every inventory entry requires its stable upstream ID"
                    )
                previous = connection.execute(
                    "SELECT document,position FROM maintenance_items WHERE run_id=? AND upstream_id=?",
                    (identifier, upstream),
                ).fetchone()
                if previous:
                    if previous[0] != _json(entry) or previous[1] != index:
                        raise _error(
                            "inventory_input_conflict", "A repeated inventory batch changed its input", 409
                        )
                    continue
                connection.execute(
                    "INSERT INTO maintenance_items VALUES(?,?,?,?)",
                    (identifier, upstream, index, _json(entry)),
                )
            record["coverage"]["discovered"] = connection.execute(
                "SELECT COUNT(*) FROM maintenance_items WHERE run_id=?", (identifier,)
            ).fetchone()[0]
            record.update(status="running", phase="extracting")
            self._save(connection, record)
            return record

    def complete_inventory(self, identifier, document):
        with self._lock:
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                self._active(record)
                rows = connection.execute(
                    "SELECT position,document FROM maintenance_items WHERE run_id=? ORDER BY position",
                    (identifier,),
                ).fetchall()
            if record["inventoryId"] and not document.get("refresh"):
                return record
            if [row[0] for row in rows] != list(range(len(rows))):
                raise _error(
                    "inventory_gap", "Inventory batches do not form a contiguous complete listing", 409
                )
            usage = document.get("usage", {})
            for key in ("requests", "networkBytes", "previewBytes"):
                amount = usage.get(key)
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                    raise _error(
                        "invalid_maintenance_usage",
                        "Source execution must report actual request and byte counts",
                    )
                charged = amount - usage.get("embeddedPreviewBytes", 0) if key == "previewBytes" else amount
                if charged > record["budgets"][key] and not document.get("sourceError"):
                    return self.fail(
                        identifier, "source_budget", "Source usage exceeded the run budget", budget=True
                    )
            source_revision = document.get("revision") or record.get("sourceRevision")
            if record.get("sourceRevision") not in (None, source_revision):
                raise _error(
                    "source_revision_conflict",
                    "Inventory refresh cannot change its fixed source revision",
                    409,
                )
            extraction_complete = document.get("extractionComplete", True) is True
            counts = copy.deepcopy(document.get("counts") or record.get("inventoryCounts") or {})
            with self.store.transaction() as connection:
                if extraction_complete:
                    connection.executemany(
                        "INSERT OR IGNORE INTO maintenance_extracted VALUES(?,?,?,?)",
                        (
                            (
                                identifier,
                                json.loads(row[1])["upstreamId"],
                                _digest(json.loads(row[1])),
                                "extracted",
                            )
                            for row in rows
                        ),
                    )
                extracted = connection.execute(
                    "SELECT COUNT(*) FROM maintenance_extracted WHERE run_id=?", (identifier,)
                ).fetchone()[0]
                extraction_failed = connection.execute(
                    "SELECT COUNT(*) FROM maintenance_extracted WHERE run_id=? AND status='failed'",
                    (identifier,),
                ).fetchone()[0]
            counts.update(
                metadataProcessed=extracted,
                metadataExtracted=extracted - extraction_failed,
                metadataFailed=extraction_failed,
                metadataPending=len(rows) - extracted,
            )
            inventory = self.store.stage_inventory(
                record["collectionId"],
                source_revision,
                [json.loads(row[1]) for row in rows],
                complete=document.get("complete") is True,
                counts=counts,
                evidence=document.get("evidence"),
            )
            inventory_id = inventory["id"]
            pending = [
                entry
                for entry in self.store.inventory_entries(inventory_id)
                if entry["organization"]["status"] != "organized"
            ]
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                record.update(
                    inventoryId=inventory_id,
                    sourceRevision=source_revision,
                    inventoryCounts=counts,
                    extractionComplete=extraction_complete,
                    sourceError=document.get("sourceError"),
                    inventoryIdentityDigest=_digest(sorted(json.loads(row[1])["upstreamId"] for row in rows)),
                )
                if (document.get("sourceError") or {}).get("code") == "budget_exhausted":
                    record["budgetExhausted"] = True
                record["usage"].update(
                    {
                        key: usage.get(key, 0)
                        for key in ("requests", "networkBytes", "previewBytes", "embeddedPreviewBytes")
                    }
                )
                record["sourceUsage"] = copy.deepcopy(usage)
                record["coverage"].update(
                    indexed=len(rows),
                    reused=len(rows) - len(pending),
                    extracted=extracted - extraction_failed,
                    extractionFailed=extraction_failed,
                    extractionPending=len(rows) - extracted,
                )
                record["phase"] = "organizing" if extraction_complete else "extracting"
                connection.execute("DELETE FROM maintenance_tasks WHERE run_id=?", (identifier,))
                for item in pending:
                    connection.execute(
                        "INSERT INTO maintenance_tasks VALUES(?,?,?,?)",
                        (identifier, item["upstreamId"], "pending", _json(item)),
                    )
                self._save(connection, record)
                return record

    def ingest_extracted(self, identifier, entries):
        if not isinstance(entries, list) or not 1 <= len(entries) <= 24:
            raise _error("invalid_extraction_batch", "Extraction updates contain 1 through 24 known entries")
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            if record["inventoryId"] is None or record.get("extractionComplete"):
                raise _error(
                    "extraction_not_active", "Stage the complete identity inventory before extraction", 409
                )
            for entry in entries:
                upstream = entry.get("upstreamId") if isinstance(entry, dict) else None
                known = connection.execute(
                    "SELECT 1 FROM maintenance_items WHERE run_id=? AND upstream_id=?", (identifier, upstream)
                ).fetchone()
                if known is None:
                    raise _error(
                        "unknown_extraction_identity",
                        "Extraction cannot add identities outside its manifest",
                        409,
                    )
                connection.execute(
                    "UPDATE maintenance_items SET document=? WHERE run_id=? AND upstream_id=?",
                    (_json(entry), identifier, upstream),
                )
                status = entry.get("extractionStatus", "extracted")
                if status not in ("extracted", "failed"):
                    raise _error("invalid_extraction_status", "Extraction status must be extracted or failed")
                connection.execute(
                    "INSERT INTO maintenance_extracted VALUES(?,?,?,?) ON CONFLICT(run_id,upstream_id) "
                    "DO UPDATE SET input_digest=excluded.input_digest,status=excluded.status",
                    (identifier, upstream, _digest(entry), status),
                )
            extracted = connection.execute(
                "SELECT COUNT(*) FROM maintenance_extracted WHERE run_id=?", (identifier,)
            ).fetchone()[0]
            failed = connection.execute(
                "SELECT COUNT(*) FROM maintenance_extracted WHERE run_id=? AND status='failed'", (identifier,)
            ).fetchone()[0]
            record["coverage"].update(
                extracted=extracted - failed,
                extractionFailed=failed,
                extractionPending=record["coverage"]["indexed"] - extracted,
            )
            self._save(connection, record)
            return record

    def publish_inventory(self, identifier):
        with self._lock:
            record = self.show(identifier)
            self._active(record)
            if not record["inventoryId"]:
                raise _error("inventory_not_ready", "Complete identity enumeration before publication", 409)
            release = self.publish([record["inventoryId"]])
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                record["releaseId"] = release["id"]
                self._save(connection, record)
                return record

    def claim(self, identifier, *, limit=24):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 24:
            raise _error("invalid_batch_limit", "Batch limit must be 1 through 24")
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            if not record["inventoryId"]:
                raise _error(
                    "inventory_not_ready",
                    "Complete identity enumeration before claiming organization work",
                    409,
                )
            if record["budgetExhausted"]:
                return {"entries": [], "budgetExhausted": True}
            if not record.get("extractionComplete", True):
                raise _error(
                    "source_extraction_in_progress",
                    "Finish the bounded source extraction before organization",
                    409,
                )
            elapsed = time.time() - record["createdAt"]
            if elapsed >= record["budgets"]["seconds"] or (
                record["runtime"]["configured"]
                and (record["usage"]["modelTokens"] or 0) >= record["budgets"]["modelTokens"]
            ):
                record["budgetExhausted"] = True
                self._save(connection, record)
                return {"entries": [], "budgetExhausted": True}
            rows = connection.execute(
                "SELECT upstream_id,document FROM maintenance_tasks WHERE run_id=? "
                "AND state='pending' ORDER BY upstream_id LIMIT ?",
                (identifier, limit),
            ).fetchall()
            if not rows:
                return {"entries": [], "budgetExhausted": False}
            entries = [json.loads(row[1]) for row in rows]
            while len(entries) > 1 and len(_json(entries).encode("utf-8")) > 1048576:
                entries.pop()
            rows = rows[: len(entries)]
            batch = {
                "id": "batch-" + uuid.uuid4().hex,
                "runId": identifier,
                "entries": entries,
                "versions": record["versions"],
                "inventoryId": record["inventoryId"],
                "remaining": {
                    "seconds": max(0, record["budgets"]["seconds"] - (time.time() - record["createdAt"])),
                    "modelTokens": max(
                        0, record["budgets"]["modelTokens"] - (record["usage"]["modelTokens"] or 0)
                    ),
                },
            }
            batch["inputKey"] = _digest(
                {key: batch[key] for key in ("runId", "entries", "versions", "inventoryId")}
            )
            connection.execute(
                "INSERT INTO maintenance_batches VALUES(?,?,?,?)",
                (batch["id"], identifier, "claimed", _json(batch)),
            )
            for row in rows:
                connection.execute(
                    "UPDATE maintenance_tasks SET state='claimed' WHERE run_id=? AND upstream_id=?",
                    (identifier, row[0]),
                )
            return batch

    def submit(self, identifier, batch_id, document):
        with self._lock:
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                row = connection.execute(
                    "SELECT state,document FROM maintenance_batches WHERE id=? AND run_id=?",
                    (batch_id, identifier),
                ).fetchone()
            if row is None:
                raise _error("unknown_maintenance_batch", "Unknown run batch", 404)
            batch = json.loads(row[1])
            digest = _digest(document)
            if row[0] == "submitted":
                if batch["submissionDigest"] != digest:
                    raise _error(
                        "batch_submission_conflict",
                        "Batch was already submitted with a different result",
                        409,
                    )
                return batch["response"]
            self._active(record)
            if row[0] == "submitting" and batch["submissionDigest"] != digest:
                raise _error(
                    "batch_submission_conflict", "A retained execution receipt already owns this batch", 409
                )
            if row[0] == "failed":
                raise _error(
                    "batch_failed", "Resume or retry the failed batch before submitting results", 409
                )
            if document.get("inputKey") != batch["inputKey"]:
                raise _error("batch_input_conflict", "Submission does not match its claimed input", 409)
            results = document.get("results")
            if not isinstance(results, list) or len(results) != len(batch["entries"]):
                raise _error("invalid_batch_result", "Submit one structured result per claimed entry")
            by_id = {item.get("upstreamId"): item for item in results if isinstance(item, dict)}
            if set(by_id) != {entry["upstreamId"] for entry in batch["entries"]}:
                raise _error("invalid_batch_result", "Batch results must match the exact claimed identities")
            tokens = _tokens(document)
            execution = document.get("execution") or {}
            if not isinstance(execution, dict):
                raise _error("invalid_execution_receipt", "Execution evidence must be an object")
            original = execution.get("original")
            if original is not None and (
                not isinstance(original, str)
                or execution.get("originalSha256") != hashlib.sha256(original.encode("utf-8")).hexdigest()
            ):
                raise _error(
                    "invalid_execution_receipt", "Original execution receipt does not match its digest"
                )
            for entry in batch["entries"]:
                if by_id[entry["upstreamId"]].get("inputDigest") != entry["inputDigest"]:
                    raise _error("batch_input_conflict", "Entry input digest changed", 409)
            # Save the original execution receipt before changing any entry.
            # Recovery replays the same core submission keys and accounts once.
            batch.update(submissionDigest=digest, receipt=document)
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                if not batch.get("usageAccounted"):
                    previous_calls = batch.get("modelCallsBefore", record["usage"]["modelCalls"])
                    previous_tokens = batch.get("modelTokensBefore", record["usage"]["modelTokens"])
                    record["usage"]["modelCalls"] = None if previous_calls is None else previous_calls + 1
                    record["usage"]["modelTokens"] = (
                        None
                        if tokens is None
                        or previous_calls is None
                        or (previous_tokens is None and previous_calls > 0)
                        else (previous_tokens or 0) + tokens
                    )
                    record["budgetExhausted"] = (
                        tokens is None
                        or (record["usage"]["modelTokens"] or 0) >= record["budgets"]["modelTokens"]
                    )
                    batch["usageAccounted"] = True
                    self._save(connection, record)
                connection.execute(
                    "UPDATE maintenance_batches SET state='submitting',document=? WHERE id=?",
                    (_json(batch), batch_id),
                )
            saved_receipt = self.store.put_blob(
                (original if original is not None else _json(document)).encode("utf-8"),
                kind="runtime-receipt",
            )
            entry_receipt = {
                "usage": document.get("usage"),
                "nativeSessionId": document.get("nativeSessionId"),
                "execution": {key: value for key, value in execution.items() if key != "original"},
                "evidence": saved_receipt,
            }
            outcomes = []
            for entry in batch["entries"]:
                result = by_id[entry["upstreamId"]]
                if "error" in result:
                    outcomes.append(
                        {
                            "upstreamId": entry["upstreamId"],
                            "status": "failed",
                            "error": str(result["error"])[:500],
                        }
                    )
                    continue
                try:
                    if not result.get("fields"):
                        raise _error(
                            "empty_organization", "Organization must submit grounded descriptive fields"
                        )
                    self.store.submit_organization(
                        batch["inventoryId"],
                        entry["upstreamId"],
                        result.get("fields", {}),
                        input_digest=entry["inputDigest"],
                        base_version=entry.get("organization", {}).get("version", 0),
                        correction_version=entry.get("correctionVersion", 0),
                        versions=batch["versions"],
                        receipt=entry_receipt,
                        submission_key=batch_id + ":" + entry["upstreamId"],
                    )
                    outcomes.append({"upstreamId": entry["upstreamId"], "status": "succeeded"})
                except MetadataError as exc:
                    outcomes.append(
                        {"upstreamId": entry["upstreamId"], "status": "failed", "error": exc.code}
                    )
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                for outcome in outcomes:
                    connection.execute(
                        "UPDATE maintenance_tasks SET state=? WHERE run_id=? AND upstream_id=?",
                        (outcome["status"], identifier, outcome["upstreamId"]),
                    )
                record["coverage"]["organized"] += sum(item["status"] == "succeeded" for item in outcomes)
                record["coverage"]["failed"] += sum(item["status"] == "failed" for item in outcomes)
                response = {
                    "outcomes": outcomes,
                    "budgetExhausted": record["budgetExhausted"],
                    "usage": copy.deepcopy(record["usage"]),
                }
                batch.update(submissionDigest=digest, response=response, receipt=document)
                connection.execute(
                    "UPDATE maintenance_batches SET state='submitted',document=? WHERE id=?",
                    (_json(batch), batch_id),
                )
                self._save(connection, record)
            return response

    def execution_start(self, identifier, batch_id):
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            row = connection.execute(
                "SELECT state,document FROM maintenance_batches WHERE id=? AND run_id=?",
                (batch_id, identifier),
            ).fetchone()
            if row is None or row[0] != "claimed":
                raise _error("batch_not_claimed", "Only a claimed batch can reserve runtime execution", 409)
            batch = json.loads(row[1])
            if not batch.get("executionReserved"):
                batch.update(
                    executionReserved=True,
                    modelCallsBefore=record["usage"]["modelCalls"],
                    modelTokensBefore=record["usage"]["modelTokens"],
                )
                record["usage"].update(modelCalls=None, modelTokens=None)
                record["message"] = "Runtime execution reserved; its actual receipt and usage are pending"
                connection.execute(
                    "UPDATE maintenance_batches SET document=? WHERE id=?", (_json(batch), batch_id)
                )
                self._save(connection, record)
            return {"reserved": True}

    def batch_error(self, identifier, batch_id, code, message, *, execution_started=None):
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            row = connection.execute(
                "SELECT state,document FROM maintenance_batches WHERE id=? AND run_id=?",
                (batch_id, identifier),
            ).fetchone()
            if row is None:
                raise _error("unknown_maintenance_batch", "Unknown run batch", 404)
            if row[0] != "claimed":
                return record
            batch = json.loads(row[1])
            batch["error"] = {"code": code, "message": message[:500]}
            for entry in batch["entries"]:
                connection.execute(
                    "UPDATE maintenance_tasks SET state='failed' WHERE run_id=? AND upstream_id=?",
                    (identifier, entry["upstreamId"]),
                )
            connection.execute(
                "UPDATE maintenance_batches SET state='failed',document=? WHERE id=?",
                (_json(batch), batch_id),
            )
            record["coverage"]["failed"] += len(batch["entries"])
            # A failed execution may still have incurred tokens; retain unknown.
            if execution_started is False:
                record["usage"]["modelCalls"] = batch.get("modelCallsBefore", record["usage"]["modelCalls"])
                record["usage"]["modelTokens"] = batch.get(
                    "modelTokensBefore", record["usage"]["modelTokens"]
                )
            else:
                record["usage"].update(modelCalls=None, modelTokens=None)
            record["budgetExhausted"] = True
            record["message"] = (
                "Agent execution failed; actual model use is unknown, so further calls stopped"
            )
            self._save(connection, record)
            return record

    def finish(self, identifier):
        with self._lock:
            record = self.show(identifier)
            self._active(record)
            if not record["inventoryId"]:
                raise _error("inventory_not_ready", "A run cannot publish before enumeration closes", 409)
            with self.store.transaction() as connection:
                claimed = connection.execute(
                    "SELECT COUNT(*) FROM maintenance_tasks WHERE run_id=? AND state='claimed'", (identifier,)
                ).fetchone()[0]
            if claimed:
                raise _error(
                    "batches_in_progress", "Complete or report claimed batches before publication", 409
                )
            self.phase(identifier, "checking")
            release = self.publish([record["inventoryId"]])
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                pending = connection.execute(
                    "SELECT COUNT(*) FROM maintenance_tasks WHERE run_id=? AND state!='succeeded'",
                    (identifier,),
                ).fetchone()[0]
                pending += record["coverage"].get("extractionPending", 0) + record["coverage"].get(
                    "extractionFailed", 0
                )
                status = (
                    "budget_exhausted"
                    if record["budgetExhausted"]
                    else ("partial" if pending else "succeeded")
                )
                record.update(
                    status=status, phase="finished", releaseId=release["id"], finishedAt=time.time()
                )
                record["usage"]["seconds"] = record["finishedAt"] - record["createdAt"]
                if pending and not record["runtime"]["configured"]:
                    record["message"] = (
                        "Complete identity inventory published; organization awaits an Agent Runtime profile"
                    )
                else:
                    record["message"] = (
                        "Published an immutable catalog release with recorded maintenance coverage"
                    )
                self._save(connection, record, token_hash="")
                return record

    def publish(self, inventory_ids=None):
        options = {"index_builder": self.index_builder}
        if self.release_preparer is not None:
            options["release_preparer"] = self.release_preparer
        return self.store.publish(inventory_ids, **options)

    def restore(self, document):
        with self._lock:
            with self.store.transaction() as connection:
                active = connection.execute(
                    "SELECT COUNT(*) FROM maintenance_runs WHERE status IN ('queued','running')"
                ).fetchone()[0]
            if active:
                raise _error(
                    "maintenance_in_progress", "Cancel active maintenance before restoring metadata", 409
                )
            options = {"release_preparer": self.release_preparer} if self.release_preparer else {}
            return self.store.restore(document, **options)

    def fail(self, identifier, code, message, *, budget=False):
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            if record["status"] not in ACTIVE:
                return record
            record.update(
                status="budget_exhausted" if budget else "failed",
                phase="finished",
                finishedAt=time.time(),
                error={"code": code, "message": message[:500]},
                message=message[:500],
            )
            record["usage"]["seconds"] = record["finishedAt"] - record["createdAt"]
            self._save(connection, record, token_hash="")
            return record

    async def cancel(self, identifier):
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            if record["status"] not in ACTIVE:
                return record
            record.update(
                status="cancelled",
                phase="cancelled",
                finishedAt=time.time(),
                message="Cancelled; completed source and organization checkpoints were retained",
            )
            record["usage"]["seconds"] = record["finishedAt"] - record["createdAt"]
            self._save(connection, record, token_hash="")
        await self._stop_process(identifier)
        return record

    async def resume(self, identifier, *, callback_url=None):
        token = "lw-meta-" + secrets.token_urlsafe(32)
        previous = self.show(identifier)
        if previous["usage"]["modelCalls"] is None or (
            previous["usage"]["modelCalls"] > 0 and previous["usage"]["modelTokens"] is None
        ):
            raise _error(
                "execution_usage_unknown", "An execution has no final receipt; use a new bounded retry", 409
            )
        current_entries = (
            self.store.inventory_entries(previous["inventoryId"]) if previous.get("inventoryId") else []
        )
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            if record["status"] in ACTIVE:
                return record
            if record["status"] == "succeeded":
                raise _error("maintenance_completed", "Start a new source check after a successful run", 409)
            if record["runtime"] != self.runtime:
                raise _error(
                    "runtime_profile_changed", "Retry as a new run after changing the configured runtime", 409
                )
            active = connection.execute(
                "SELECT id FROM maintenance_runs WHERE collection_id=? AND status IN ('queued','running')",
                (record["collectionId"],),
            ).fetchone()
            if active:
                raise _error("maintenance_in_progress", "This collection already has an active run", 409)
            record.update(
                status="queued",
                phase="queued",
                finishedAt=None,
                budgetExhausted=False,
                message="Resumed from its durable checkpoint",
            )
            connection.execute(
                "UPDATE maintenance_tasks SET state='pending' WHERE run_id=? AND state!='succeeded'",
                (identifier,),
            )
            for entry in current_entries:
                state = "succeeded" if entry["organization"]["status"] == "organized" else "pending"
                connection.execute(
                    "UPDATE maintenance_tasks SET state=?,document=? WHERE run_id=? AND upstream_id=?",
                    (state, _json(entry), identifier, entry["upstreamId"]),
                )
            if current_entries:
                record["coverage"]["failed"] = 0
                record["coverage"]["organized"] = connection.execute(
                    "SELECT COUNT(*) FROM maintenance_tasks WHERE run_id=? AND state='succeeded'",
                    (identifier,),
                ).fetchone()[0]
            self._save(connection, record, token_hash=hashlib.sha256(token.encode()).hexdigest())
        self._launch(record, token, callback_url)
        return record

    async def _schedule(self):
        while not self._closed:
            try:
                await asyncio.sleep(30)
                if not self._callback_url:
                    continue
                for profile in self.store.collections():
                    interval = profile.get("scheduleSeconds", 0)
                    if not profile.get("enabled") or not isinstance(interval, (int, float)) or interval < 60:
                        continue
                    recent = next(
                        (run for run in self.status()["runs"] if run["collectionId"] == profile["id"]), None
                    )
                    if recent and (
                        recent["status"] in ACTIVE or time.time() - recent["updatedAt"] < interval
                    ):
                        continue
                    try:
                        await self.start(profile["id"], callback_url=self._callback_url)
                    except MetadataError:
                        pass
            except asyncio.CancelledError:
                break

    async def close(self):
        self._closed = True
        if self._scheduler:
            self._scheduler.cancel()
            await asyncio.gather(self._scheduler, return_exceptions=True)
        for identifier in list(self._supervisors):
            await self._stop_process(identifier)
            self.fail(identifier, "gateway_stopped", "Gateway stopped; resume the retained checkpoint")
        tasks = list(self._supervisors.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
