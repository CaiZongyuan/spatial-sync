"""Local maintenance supervisor; only this trusted host opens the metadata DB."""

from __future__ import annotations

import asyncio
import base64
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
from packages.catalog_metadata.ownership import CatalogHostLease, WorkerTree
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
    "thumbnailBytes": 536870912,
    "modelTokens": 100000,
    "seconds": 1800,
    "timeout": 20,
    "attempts": 3,
}
BUDGET_MAXIMUMS = {**DEFAULT_BUDGETS, "previewBytes": 536870912, "networkBytes": 1073741824,
                   "seconds": 86400}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _error(code, message, status=400):
    return MetadataError(code, message, status=status)


def validate_budgets(value=None):
    if value is not None and not isinstance(value, dict):
        raise _error("invalid_maintenance_budget", "Maintenance budgets must be an object")
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
        self._source_fallbacks = {}
        self._worker_slots = asyncio.Semaphore(2)
        self._scheduler = None
        self._closed = False
        self._callback_url = os.environ.get("LW_CATALOG_WORKER_URL")
        self._lease = CatalogHostLease(self.root / "host.lock")
        self._started = False

    async def startup(self):
        self._lease.acquire()
        self._started, self._closed = True, False
        try:
            await self._startup_owned()
        except BaseException:
            await self.close()
            raise

    async def _startup_owned(self):
        with self.store.preview_transaction() as connection:
            self.store.recover_thumbnail_files(connection)
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
                CREATE TABLE IF NOT EXISTS maintenance_previews (
                    run_id TEXT NOT NULL, upstream_id TEXT NOT NULL, state TEXT NOT NULL,
                    document TEXT NOT NULL, PRIMARY KEY(run_id,upstream_id)
                );
            """)
            recovering = connection.execute(
                "SELECT run_id,id,document FROM maintenance_batches WHERE state='submitting'"
            ).fetchall()
        for row in recovering:
            batch = json.loads(row[2])
            try:
                if self.show(row[0])["status"] != "cancelled":
                    self.submit(row[0], row[1], batch["receipt"], recovery=True)
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
        if record["status"] in TERMINAL:
            self._source_fallbacks.pop(record["id"], None)
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
        with self.store.transaction(write=False) as connection:
            return self._load(connection, identifier)

    def status(self):
        with self.store.transaction(write=False) as connection:
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
            "capabilities": {"externalOrganization": True, "localThumbnails": True, "nativeQueries": True},
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
                "await-agent",
                "previews/claim",
                "previews/submit",
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

    async def start(self, collection_id, *, revision=None, budgets=None, callback_url=None, request_id=None,
                    organizer="runtime", previews=False):
        if organizer not in ("runtime", "external", "none") or type(previews) is not bool:
            raise _error("invalid_maintenance_mode", "Select runtime/external/none and boolean previews")
        if revision is not None and (
            not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)
        ):
            raise _error("invalid_revision", "Maintenance requires a fixed dataset revision")
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 160):
            raise _error("invalid_request_id", "Maintenance request IDs contain 1 through 160 characters")
        collection = self.store.collection(collection_id)
        if not collection.get("enabled", False):
            raise _error("collection_disabled", "Enable the collection before maintenance", 409)
        selected = validate_budgets(budgets)
        if organizer != "runtime":
            if budgets and budgets.get("modelTokens", 0):
                raise _error(
                    "external_model_budget", "Use Runtime mode for a service-enforced model token budget"
                )
            selected["modelTokens"] = 0
        if previews and (budgets is None or "previewBytes" not in budgets):
            selected["previewBytes"] = 16 * 1024 * 1024
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
                        if (previous["requestedRevision"] != revision or previous["budgets"] != selected
                                or previous.get("organizer", "runtime") != organizer
                                or previous.get("previews", False) != previews):
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
                "organizer": organizer,
                "modelPolicy": "runtime"
                if organizer == "runtime"
                else "external"
                if organizer == "external"
                else "none",
                "previews": previews,
                "sourceWorkerComplete": False,
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
                "runtime": self.runtime if organizer == "runtime" else public_profile(None),
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
        for credential in ("LW_TOKEN", "LW_CATALOG_OPERATOR_TOKEN"):
            env.pop(credential, None)
        env["LW_CATALOG_RUN_TOKEN"] = token
        if self.profile is not None and self.show(identifier).get("organizer", "runtime") == "runtime":
            env["LW_CATALOG_AGENT_PROFILE"] = _json(self.profile)
        else:
            env.pop("LW_CATALOG_AGENT_PROFILE", None)
        cache_root = self.store.root / "blobs"
        log_root = self.root / identifier
        log_root.mkdir(parents=True, exist_ok=True)
        tree = None
        try:
            with (log_root / "worker.log").open("ab") as output:
                control_file = log_root / "cancel"
                control_file.unlink(missing_ok=True)
                ready_file = log_root / "guardian.ready"
                ready_file.unlink(missing_ok=True)
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
                    "--guardian-ready",
                    str(ready_file),
                    "--parent-pid",
                    str(os.getpid()),
                    cwd=Path(__file__).resolve().parents[2],
                    env=env,
                    stdout=output,
                    stderr=output,
                    creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
                    start_new_session=os.name != "nt",
                )
                self._processes[identifier] = process
                tree = WorkerTree(process)
                ready_file.write_text("ready", encoding="utf-8")
                try:
                    current = self.show(identifier)
                    remaining = max(0, current["budgets"]["seconds"] - max(
                        current["usage"].get("seconds", 0), time.time() - current["createdAt"]))
                    await asyncio.wait_for(process.wait(), timeout=remaining + 10)
                except TimeoutError:
                    await self._stop_process(identifier)
                    self.fail(identifier, "runtime_limit", "Worker exceeded its runtime budget", budget=True)
                else:
                    current = self.show(identifier)
                    if current["status"] in ACTIVE and not (
                        current.get("organizer") == "external" and current.get("sourceWorkerComplete")
                        and current["phase"] == "awaiting_agent"
                    ):
                        self.fail(identifier, "worker_exited", "Worker exited before publishing its result")
        except asyncio.CancelledError:
            await self._stop_process(identifier)
            raise
        except OSError:
            await self._stop_process(identifier)
            self.fail(identifier, "worker_start_failed", "Unable to start the local metadata worker")
        finally:
            if tree is not None:
                tree.close()
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
        if phase not in ("enumerating", "extracting", "organizing", "previews", "checking", "publishing"):
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

    def _source_fallback(self, identifier):
        if identifier in self._source_fallbacks:
            return self._source_fallbacks[identifier]
        record = self.show(identifier)
        latest = self.store.latest_release()
        previous = next(
            (
                item
                for item in (latest or {}).get("collections", [])
                if item["id"] == record["collectionId"] and item["revision"] == record.get("sourceRevision")
            ),
            None,
        )
        result = {"entries": {}, "complete": False}
        if previous:
            result["entries"] = {
                entry["upstreamId"]: entry
                for entry in self.store.inventory_source_entries(previous["inventoryId"])
            }
            coverage = previous.get("coverage", {})
            result["complete"] = (
                coverage.get("metadataPending") == 0 and coverage.get("metadataFailed", 0) == 0
            )
        if len(self._source_fallbacks) >= 4:
            self._source_fallbacks.pop(next(iter(self._source_fallbacks)))
        self._source_fallbacks[identifier] = result
        return result

    def ingest(self, identifier, entries, offset, *, minimal=False):
        if (
            not isinstance(entries, list)
            or not 1 <= len(entries) <= 24
            or not isinstance(offset, int)
            or offset < 0
        ):
            raise _error(
                "invalid_inventory_batch", "Inventory batches contain at most 24 entries and an offset"
            )
        fallback = self._source_fallback(identifier) if minimal else {"entries": {}}
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
                previous_source = fallback["entries"].get(upstream)
                if previous_source is not None:
                    entry = previous_source
                    if fallback["complete"] or entry.get("extractionStatus") == "extracted":
                        connection.execute(
                            "INSERT OR IGNORE INTO maintenance_extracted VALUES(?,?,?,?)",
                            (identifier, upstream, _digest(entry), "extracted"),
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
                for entry in self.store.inventory_entries(inventory_id, versions=record["versions"])
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
                if record.get("previews") and extraction_complete:
                    for row in self.store.inventory_source_entries(inventory_id):
                        connection.execute(
                            "INSERT OR IGNORE INTO maintenance_previews VALUES(?,?,?,?)",
                            (
                                identifier,
                                row["upstreamId"],
                                "pending",
                                _json(
                                    {
                                        key: row[key]
                                        for key in (
                                            "id",
                                            "collectionId",
                                            "upstreamId",
                                            "revision",
                                            "inputDigest",
                                            "preview",
                                            "evidence",
                                        )
                                    }
                                ),
                            ),
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
            latest = self.store.latest_release()
            previous = next(
                (
                    item
                    for item in (latest or {}).get("collections", [])
                    if item["id"] == record["collectionId"]
                    and item["revision"] == record.get("sourceRevision")
                ),
                None,
            )
            release = (
                latest if previous else self.publish([record["inventoryId"]], versions=record["versions"])
            )
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                record["releaseId"] = release["id"]
                self._save(connection, record)
                return record

    @staticmethod
    def _preview_coverage(connection, identifier):
        rows = connection.execute(
            "SELECT state,json_extract(document,'$.result.state'),"
            "COALESCE(json_extract(document,'$.reused'),0),COUNT(*) "
            "FROM maintenance_previews WHERE run_id=? GROUP BY 1,2,3", (identifier,)
        ).fetchall()
        return {
            "available": sum(row[3] for row in rows if row[1] == "available"),
            "missing": sum(row[3] for row in rows if row[1] == "missing"),
            "failed": sum(row[3] for row in rows if row[0] == "failed"),
            "pending": sum(row[3] for row in rows if row[0] in ("pending", "claimed")),
            "reused": sum(row[3] for row in rows if row[2]),
        }

    def preview_claim(self, identifier, *, limit=24):
        if type(limit) is not int or not 1 <= limit <= 24:
            raise _error("invalid_batch_limit", "Preview batch limit must be 1..24")
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            if not record.get("previews") or not record.get("extractionComplete"):
                raise _error(
                    "previews_not_ready", "Request previews and complete source extraction first", 409
                )
            if record["budgetExhausted"]:
                return {"entries": [], "budgetExhausted": True}
            if time.time() - record["createdAt"] >= record["budgets"]["seconds"]:
                record["budgetExhausted"] = True
                self._save(connection, record)
                return {"entries": [], "budgetExhausted": True}
            rows = connection.execute(
                "SELECT upstream_id,document FROM maintenance_previews WHERE run_id=? AND state='pending' "
                "ORDER BY upstream_id LIMIT ?", (identifier, limit),
            ).fetchall()
            entries = []
            for upstream, document in rows:
                entry = json.loads(document)
                saved = self.store.entry_preview(entry)
                if saved and saved["state"] in ("available", "missing", "suspect"):
                    entry.update(result=saved, reused=True)
                    state = "succeeded"
                else:
                    entry["retry"] = bool(saved and saved["state"] == "failed"
                                          and saved.get("reason") != "local_thumbnail_unavailable")
                    entries.append(entry)
                    state = "claimed"
                connection.execute(
                    "UPDATE maintenance_previews SET state=?,document=? WHERE run_id=? AND upstream_id=?",
                    (state, _json(entry), identifier, upstream),
                )
            record["previewCoverage"] = self._preview_coverage(connection, identifier)
            self._save(connection, record)
            return {"entries": entries, "remaining": record["previewCoverage"]["pending"],
                    "coverage": record["previewCoverage"], "budgetExhausted": False}

    def preview_submit(self, identifier, upstream_id, document):
        with self._lock:
            record = self.show(identifier)
            self._active(record)
            with self.store.transaction(write=False) as connection:
                row = connection.execute(
                    "SELECT state,document FROM maintenance_previews WHERE run_id=? AND upstream_id=?",
                    (identifier, upstream_id),
                ).fetchone()
            if row is None:
                raise _error("unknown_preview_task", "No claimed preview task has this identity", 404)
            entry = json.loads(row[1])
            submission = _digest(document)
            if entry.get("submissionDigest") == submission:
                return record
            if row[0] != "claimed" or document.get("inputDigest") != entry["inputDigest"]:
                raise _error(
                    "preview_input_conflict", "Preview submission differs from its active input", 409
                )
            usage = document.get("usage")
            if usage is not None:
                previous = record.get("sourceUsage", {})
                if any(
                    usage.get(key, -1) < previous.get(key, 0)
                    for key in ("requests", "networkBytes", "previewBytes")
                ):
                    raise _error("invalid_maintenance_usage", "Preview source usage must be cumulative")
                record = self.checkpoint(identifier, usage)
            state = document.get("state")
            if state not in ("available", "missing", "failed", "suspect", "deferred"):
                raise _error("invalid_preview_state", "Return an image or a specific preview gap")
            exceeded = any(
                (record["usage"].get(key) or 0) - (record["usage"].get("embeddedPreviewBytes", 0)
                  if key == "previewBytes" else 0) > record["budgets"][key]
                for key in ("requests", "networkBytes", "previewBytes")
            )
            added, result = 0, None
            with self.store.preview_transaction() as connection:
                if state == "deferred" or exceeded:
                    task_state, record["budgetExhausted"] = "pending", True
                else:
                    raw = None
                    if state == "available":
                        try:
                            raw = base64.b64decode(document.get("data", ""), validate=True)
                        except (ValueError, TypeError) as exc:
                            raise _error(
                                "invalid_preview", "Preview payload must be base64 image bytes"
                            ) from exc
                    try:
                        result, added = self.store.save_preview(
                            entry, raw, state=state, reason=document.get("reason"), connection=connection,
                            max_added_bytes=record["budgets"].get("thumbnailBytes", 536870912)
                            - record.get("thumbnailBytes", 0),
                        )
                        task_state = "failed" if state == "failed" else "succeeded"
                    except MetadataError as exc:
                        if exc.code in ("thumbnail_budget", "thumbnail_storage_limit"):
                            task_state, record["budgetExhausted"] = "pending", True
                            record["message"] = str(exc)
                        else:
                            result, _ = self.store.save_preview(
                                entry, state="failed", reason=exc.code, connection=connection,
                            )
                            task_state = "failed"
                if result is not None:
                    entry["result"] = result
                entry["submissionDigest"] = submission
                connection.execute(
                    "UPDATE maintenance_previews SET state=?,document=? WHERE run_id=? AND upstream_id=?",
                    (task_state, _json(entry), identifier, upstream_id),
                )
                record["thumbnailBytes"] = record.get("thumbnailBytes", 0) + added
                record["previewCoverage"] = self._preview_coverage(connection, identifier)
                self._save(connection, record)
            return record

    def await_agent(self, identifier):
        with self._lock:
            record = self.show(identifier)
            self._active(record)
            if record.get("organizer") != "external" or not record.get("extractionComplete"):
                raise _error(
                    "external_organization_unavailable", "Select external organization after extraction", 409
                )
            with self.store.transaction(write=False) as connection:
                if record.get("previews") and self._preview_coverage(connection, identifier)["pending"]:
                    raise _error(
                        "previews_in_progress", "Complete source previews before external organization", 409
                    )
            release = self.publish([record["inventoryId"]], versions=record["versions"])
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                record.update(
                    phase="awaiting_agent",
                    sourceWorkerComplete=True,
                    releaseId=release["id"],
                    message="Source work is complete; an external Agent can claim organization batches",
                )
                self._save(connection, record)
                return record

    def claim(self, identifier, *, limit=24):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 24:
            raise _error("invalid_batch_limit", "Batch limit must be 1 through 24")
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            self._active(record)
            if record.get("organizer") == "external" and record["phase"] != "awaiting_agent":
                raise _error(
                    "source_worker_in_progress", "Wait for source and preview maintenance to finish", 409
                )
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
            held = connection.execute(
                "SELECT COUNT(*) FROM maintenance_batches WHERE run_id=? "
                "AND state IN ('claimed','submitting')",
                (identifier,),
            ).fetchone()[0]
            if held:
                raise _error(
                    "maintenance_batch_in_progress", "Complete the current batch before claiming another", 409
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
                "organizer": record.get("organizer", "runtime"),
                "modelPolicy": record.get("modelPolicy", "runtime"),
                "remaining": {
                    "seconds": max(0, record["budgets"]["seconds"] - (time.time() - record["createdAt"])),
                    "modelTokens": None if record.get("organizer") == "external" else max(
                        0, record["budgets"]["modelTokens"] - (record["usage"]["modelTokens"] or 0)
                    ),
                },
            }
            batch["inputKey"] = _digest(
                {key: batch[key] for key in ("runId", "entries", "versions", "inventoryId")}
            )
            vocabulary = self.store.vocabulary(record["versions"]["vocabulary"])
            categories = []
            preferred = {entry.get("category") for entry in entries}
            for term in sorted(vocabulary["categories"], key=lambda item: item["id"] not in preferred):
                value = {"id": term["id"], "name": term["name"]}
                if len(_json(categories + [value]).encode()) > 32768:
                    break
                categories.append(value)
            batch["vocabulary"] = {"version": vocabulary["version"], "categories": categories,
                                   "truncated": len(categories) < len(vocabulary["categories"])}
            batch["evidence"] = []
            seen, remaining = set(), 32768
            for entry in entries:
                for reference in entry.get("evidence", []):
                    key = reference.get("sha256")
                    if key in seen or not key:
                        continue
                    seen.add(key)
                    evidence = {name: reference.get(name) for name in ("sha256", "path", "bytes")}
                    if type(reference.get("bytes")) is int and reference["bytes"] <= min(8192, remaining):
                        raw = self.store.blob(key)
                        try:
                            evidence["text"] = raw.decode("utf-8")
                            remaining -= len(raw)
                        except UnicodeDecodeError:
                            pass
                    if len(batch["evidence"]) < 24:
                        batch["evidence"].append(evidence)
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

    def _refresh_model_usage(self, connection, record, *, settle=False):
        calls, tokens, unknown = 0, 0, False
        unknown_tokens = False
        rows = connection.execute(
            "SELECT state,document FROM maintenance_batches WHERE run_id=?", (record["id"],)
        )
        for row in rows:
            batch = json.loads(row[1])
            if "receipt" in batch:
                calls += 1
                amount = _tokens(batch["receipt"])
                if amount is None:
                    unknown_tokens = True
                else:
                    tokens += amount
            elif batch.get("executionReserved") or (row[0] == "failed" and not batch.get("didNotExecute")):
                unknown = True
        if record.get("organizer") == "external":
            record["usage"].update(
                modelCalls=None, modelTokens=None, externalBatches=calls,
                externalReportedTokens=None if unknown or unknown_tokens or not calls else tokens,
            )
            return
        record["usage"]["modelCalls"] = None if unknown else calls
        record["usage"]["modelTokens"] = None if unknown or unknown_tokens or not calls else tokens
        if settle:
            record["budgetExhausted"] = bool(
                record["budgetExhausted"]
                or unknown
                or unknown_tokens
                or (calls and tokens >= record["budgets"]["modelTokens"])
            )

    def submit(self, identifier, batch_id, document, *, recovery=False):
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
            if not recovery:
                self._active(record)
            if row[0] not in ("claimed", "submitting"):
                raise _error(
                    "stale_maintenance_batch",
                    "This batch no longer owns active work; claim a fresh batch",
                    409,
                )
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
            _tokens(document)
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
                connection.execute(
                    "UPDATE maintenance_batches SET state='submitting',document=? WHERE id=?",
                    (_json(batch), batch_id),
                )
                self._refresh_model_usage(connection, record, settle=True)
                self._save(connection, record)
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
            if record.get("organizer") != "runtime" and record.get("organizer") is not None:
                raise _error(
                    "external_model_execution", "This run does not dispatch service-owned model calls", 409
                )
            self._active(record)
            row = connection.execute(
                "SELECT state,document FROM maintenance_batches WHERE id=? AND run_id=?",
                (batch_id, identifier),
            ).fetchone()
            if row is None or row[0] != "claimed":
                raise _error("batch_not_claimed", "Only a claimed batch can reserve runtime execution", 409)
            batch = json.loads(row[1])
            if not batch.get("executionReserved"):
                other = connection.execute(
                    "SELECT COUNT(*) FROM maintenance_batches WHERE run_id=? AND id!=? "
                    "AND state IN ('claimed','submitting')",
                    (identifier, batch_id),
                ).fetchone()[0]
                if (
                    record["budgetExhausted"]
                    or other
                    or (record["usage"]["modelTokens"] or 0) >= record["budgets"]["modelTokens"]
                ):
                    raise _error(
                        "model_budget_reserved",
                        "No unreserved model allowance remains for this execution",
                        409,
                    )
                batch["executionReserved"] = True
                record["message"] = "Runtime execution reserved; its actual receipt and usage are pending"
                connection.execute(
                    "UPDATE maintenance_batches SET document=? WHERE id=?", (_json(batch), batch_id)
                )
                self._refresh_model_usage(connection, record)
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
            batch["didNotExecute"] = execution_started is False
            if execution_started is False:
                batch["executionReserved"] = False
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
            self._refresh_model_usage(connection, record, settle=True)
            if record.get("organizer") == "external":
                record["message"] = "External batch failed; unrelated batches remain available to the caller"
            else:
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
            if (record.get("organizer") == "external" and not record.get("sourceWorkerComplete")
                    and not record["budgetExhausted"] and not record.get("sourceError")):
                raise _error("source_worker_in_progress", "Wait for the source worker before finishing", 409)
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
            release = self.publish([record["inventoryId"]], versions=record["versions"])
            with self.store.transaction() as connection:
                record = self._load(connection, identifier)
                pending = connection.execute(
                    "SELECT COUNT(*) FROM maintenance_tasks WHERE run_id=? AND state!='succeeded'",
                    (identifier,),
                ).fetchone()[0]
                pending += record["coverage"].get("extractionPending", 0) + record["coverage"].get(
                    "extractionFailed", 0
                )
                preview_coverage = self._preview_coverage(connection, identifier)
                if record.get("previews"):
                    pending += preview_coverage["pending"] + preview_coverage["failed"]
                    record["previewCoverage"] = preview_coverage
                status = (
                    "budget_exhausted"
                    if record["budgetExhausted"]
                    else ("partial" if pending else "succeeded")
                )
                record.update(
                    status=status, phase="finished", releaseId=release["id"], finishedAt=time.time()
                )
                record["usage"]["seconds"] = record["finishedAt"] - record["createdAt"]
                if pending and record.get("organizer") == "external":
                    record["message"] = (
                        "Published recorded coverage; external organization has unresolved entries"
                    )
                elif pending and record.get("organizer") == "none":
                    record["message"] = (
                        "Published source metadata and preview coverage; organization was not requested"
                    )
                elif pending and not record["runtime"]["configured"]:
                    record["message"] = (
                        "Complete identity inventory published; organization awaits an Agent Runtime profile"
                    )
                else:
                    record["message"] = (
                        "Published an immutable catalog release with recorded maintenance coverage"
                    )
                self._save(connection, record, token_hash="")
                return record

    def publish(self, inventory_ids=None, *, versions=None):
        options = {"index_builder": self.index_builder}
        if versions is not None:
            options["versions"] = versions
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
        with self.store.transaction(write=False) as connection:
            receipts = connection.execute(
                "SELECT id,document FROM maintenance_batches WHERE run_id=? AND state='submitting'",
                (identifier,),
            ).fetchall()
        for row in receipts:
            self.submit(identifier, row[0], json.loads(row[1])["receipt"], recovery=True)
        previous = self.show(identifier)
        if time.time() - previous["createdAt"] >= previous["budgets"]["seconds"]:
            raise _error("maintenance_deadline", "Original deadline elapsed; start a bounded retry", 409)
        if previous.get("organizer", "runtime") == "runtime" and (previous["usage"]["modelCalls"] is None or (
            previous["usage"]["modelCalls"] > 0 and previous["usage"]["modelTokens"] is None
        )):
            raise _error(
                "execution_usage_unknown", "An execution has no final receipt; use a new bounded retry", 409
            )
        current_entries = (
            self.store.inventory_entries(previous["inventoryId"], versions=previous["versions"])
            if previous.get("inventoryId")
            else []
        )
        with self._lock, self.store.transaction() as connection:
            record = self._load(connection, identifier)
            if record["status"] in ACTIVE:
                return record
            if record["status"] == "succeeded":
                raise _error("maintenance_completed", "Start a new source check after a successful run", 409)
            expected_runtime = (
                self.runtime if record.get("organizer", "runtime") == "runtime" else public_profile(None)
            )
            if record["runtime"] != expected_runtime:
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
                sourceWorkerComplete=False,
            )
            connection.execute(
                "UPDATE maintenance_tasks SET state='pending' WHERE run_id=? AND state!='succeeded'",
                (identifier,),
            )
            connection.execute(
                "UPDATE maintenance_previews SET state='pending' WHERE run_id=? AND state!='succeeded'",
                (identifier,),
            )
            connection.execute(
                "UPDATE maintenance_batches SET state='abandoned' WHERE run_id=? AND state='claimed'",
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
                with self.store.transaction(write=False) as connection:
                    rows = connection.execute(
                        "SELECT document FROM maintenance_runs WHERE rowid IN "
                        "(SELECT MAX(rowid) FROM maintenance_runs GROUP BY collection_id)"
                    ).fetchall()
                latest = {run["collectionId"]: run for run in (json.loads(row[0]) for row in rows)}
                for recent in latest.values():
                    if (recent["status"] in ACTIVE and recent["phase"] == "awaiting_agent"
                            and time.time() - recent["createdAt"] >= recent["budgets"]["seconds"]):
                        self.fail(
                            recent["id"],
                            "runtime_limit",
                            "External organization deadline elapsed",
                            budget=True,
                        )
                if not self._callback_url:
                    continue
                for profile in self.store.collections():
                    interval = profile.get("scheduleSeconds", 0)
                    if not profile.get("enabled") or not isinstance(interval, (int, float)) or interval < 60:
                        continue
                    recent = latest.get(profile["id"])
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
        if not self._started:
            return
        try:
            await self._close_owned()
        finally:
            self._lease.close()
            self._started = False

    async def _close_owned(self):
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
