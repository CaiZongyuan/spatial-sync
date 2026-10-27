"""Ordinary, recoverable library removals, independent of human Approvals."""

import asyncio
import re
import threading
import time
import uuid
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.library.archive import destination_parent, import_lock, package_files
from packages.library.common import LibraryError, content_hash
from packages.library.references import dependency_reports
from packages.library.reports import HASH, SUBJECT
from packages.library.transactions import durable_json, reserve_revision
from packages.protocol.schema import Actor

IDENTITY = re.compile(r"deletion-[a-f0-9]{32}")


class RemovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: Literal["asset", "scene"]
    id: str = Field(min_length=1, max_length=200)
    contentHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    requestId: str = Field(min_length=1, max_length=200)


class RemovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_: Literal["lw.library-deletion/1"] = Field(default="lw.library-deletion/1", alias="schema")
    id: str = Field(pattern=r"^deletion-[a-f0-9]{32}$")
    resource: Literal["asset", "scene"]
    versionId: str
    contentHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    requestId: str
    requestedBy: Actor
    createdAtMs: float
    status: Literal["queued", "checking", "committed", "blocked", "failed", "restoring", "restored"]
    retainedBytes: int = Field(default=0, ge=0)
    dependencies: dict[str, str] = Field(default_factory=dict)
    error: dict | None = None
    restoredBy: Actor | None = None
    supersededApprovals: list[str] = Field(default_factory=list)


class LibraryRemovals:
    def __init__(self, legacy):
        self.legacy = legacy
        self.root = legacy.root
        self.records = {}
        self.lock = threading.RLock()
        self.tasks = {}
        self.errors = set()
        self.legacy.normal_removal = self.superseding

    def directory(self, identifier):
        if not IDENTITY.fullmatch(identifier):
            raise LibraryError("unknown_deletion", "Unknown deletion record")
        return destination_parent(self.root, (".trash", identifier), create=True)

    def _read(self, identifier):
        path = self.directory(identifier) / "operation.json"
        if path.is_symlink() or path.is_junction():
            raise ValueError("Removal record cannot be a link")
        record = RemovalRecord.model_validate_json(path.read_bytes())
        if record.id != identifier or not SUBJECT.fullmatch(f"{record.resource}:{record.versionId}"):
            raise ValueError("Removal identity differs from its journal")
        if any(not HASH.fullmatch(digest) for digest in record.dependencies.values()):
            raise ValueError("Invalid retained dependency")
        return record.model_dump(mode="json", by_alias=True)

    def _save(self, record):
        value = RemovalRecord.model_validate(record).model_dump(mode="json", by_alias=True)
        with self.lock:
            try:
                durable_json(self.directory(value["id"]) / "operation.json", value)
            except OSError:
                try:
                    if self._read(value["id"]) == value:
                        self.records[value["id"]] = value
                except (OSError, ValueError, LibraryError):
                    pass
                raise
            self.records[value["id"]] = value
        return deepcopy(value)

    def show(self, identifier):
        with self.lock:
            if identifier in self.errors:
                raise LibraryError(
                    "deletion_unavailable",
                    "Execution journal needs recovery; restart or retry after storage is available",
                    503,
                    deletionId=identifier,
                )
            record = self.records.get(identifier)
            if record is None:
                raise LibraryError("unknown_deletion", "Unknown deletion record")
            return deepcopy(record)

    def list(self):
        with self.lock:
            return sorted(
                (deepcopy(record) for record in self.records.values()),
                key=lambda record: (record["createdAtMs"], record["id"]),
                reverse=True,
            )

    def submit(self, body, actor):
        subject = f"{body.resource}:{body.id}"
        if not SUBJECT.fullmatch(subject):
            raise LibraryError("unknown_version", "Deletion requires an exact version", 422)
        with self.lock:
            for record in self.records.values():
                if record["requestId"] == body.requestId and record["requestedBy"] == actor.model_dump():
                    if (record["resource"], record["versionId"], record["contentHash"]) != (
                        body.resource,
                        body.id,
                        body.contentHash,
                    ):
                        raise LibraryError(
                            "request_conflict", "requestId already names another deletion", 409
                        )
                    return deepcopy(record)
            collection = self.legacy._collection(body.resource)
            version = collection.versions.get(body.id)
            if version is None:
                raise LibraryError("unknown_version", "Unknown library version")
            if version.root != "user":
                raise LibraryError("library_read_only", "Built-in library versions are read-only", 403)
            if version.content_hash != body.contentHash:
                raise LibraryError(
                    "version_changed", "Deletion content hash differs from indexed version", 409
                )
            return self._save(
                {
                    "id": "deletion-" + uuid.uuid4().hex,
                    "resource": body.resource,
                    "versionId": body.id,
                    "contentHash": body.contentHash,
                    "requestId": body.requestId,
                    "requestedBy": actor.model_dump(),
                    "createdAtMs": time.time() * 1000,
                    "status": "queued",
                }
            )

    def start(self, identifier, *, restore=False):
        key = (identifier, restore)
        if key in self.tasks:
            return
        task = asyncio.create_task(asyncio.to_thread(self.restore if restore else self.execute, identifier))
        self.tasks[key] = task

        def finished(result):
            self.tasks.pop(key, None)
            # The journal owns errors; retrieve task failures so shutdown has no orphaned exception.
            if not result.cancelled():
                if result.exception() is not None:
                    with self.lock:
                        # A committed journal remains authoritative even if secondary audit cleanup failed.
                        if self.records[identifier]["status"] not in ("committed", "restored"):
                            self.errors.add(identifier)

        task.add_done_callback(finished)

    async def close(self):
        if self.tasks:
            pending = asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                await pending
                raise

    def superseding(self, approval):
        with self.lock:
            return next(
                (
                    record["id"]
                    for record in self.records.values()
                    if approval.approvalId in record["supersededApprovals"]
                    and approval.subjectId == f"{record['resource']}:{record['versionId']}"
                    and approval.details.get("contentHash") == record["contentHash"]
                ),
                None,
            )

    def _destination(self, record):
        prefix = "assets" if record["resource"] == "asset" else "scenes"
        return destination_parent(self.root, (prefix, *record["versionId"].replace("@", "/").split("/")))

    def _dependencies(self, version, kind):
        identifiers = set()
        for report in dependency_reports(version, kind):
            identifiers.update(item["id"] for item in report.get("referencedAssets", []))
        if kind == "scene":
            identifiers.update(version.template_assets)
            identifiers.update(version.runtime_assets)
        return {identifier: self.legacy.assets.resolve(identifier).content_hash for identifier in identifiers}

    def _retire_approvals(self, record):
        for identifier in record["supersededApprovals"]:
            approval = self.legacy.approvals.records.get(identifier)
            if approval is not None and approval.status == "pending" and self.superseding(approval):
                self.legacy.approvals._save(
                    approval.model_copy(
                        update={
                            "status": "unavailable",
                            "details": approval.details
                            | {
                                "execution": {
                                    "status": "blocked",
                                    "error": {
                                        "code": "version_withdrawn",
                                        "message": "Version moved to recovery storage",
                                        "deletionId": record["id"],
                                    },
                                },
                            },
                        }
                    )
                )

    def execute(self, identifier):
        with self.legacy.assets._lock, import_lock(self.root):
            record = self.show(identifier)
            if record["status"] != "queued":
                return record
            record = self._save(record | {"status": "checking"})
            destination = self._destination(record)
            package = self.directory(identifier) / "package"
            try:
                kind, version = self.legacy._target(f"{record['resource']}:{record['versionId']}")
                if version.content_hash != record["contentHash"]:
                    raise LibraryError("version_changed", "The requested package has changed", 409)
                self.legacy._refresh()
                inspection = self.legacy.inspection.scan(version, kind)
                self.legacy._allowed(inspection)
                dependencies = self._dependencies(version, kind)
                reserve_revision(self.root, f"{kind}:{record['versionId'].partition('@')[0]}", version.rev)
                # Revalidate raw bytes and path confinement at the move boundary.
                self.legacy._target(f"{kind}:{version.id}")
                if package.exists() or package.is_symlink() or package.is_junction():
                    raise ValueError("Recovery package already exists")
                destination.rename(package)
                package_files(package)
                if content_hash(package) != record["contentHash"]:
                    raise ValueError("Package changed during removal")
                self.legacy._refresh()
                retained = sum((package / name).stat().st_size for name in package_files(package))
                superseded = [
                    approval.approvalId
                    for approval in self.legacy.approvals.records.values()
                    if approval.subject == "deletion"
                    and approval.status == "pending"
                    and approval.subjectId == f"{kind}:{version.id}"
                    and approval.details.get("contentHash") == record["contentHash"]
                ]
                record = self._save(
                    record
                    | {
                        "status": "committed",
                        "dependencies": dependencies,
                        "retainedBytes": retained,
                        "supersededApprovals": superseded,
                    }
                )
            except (OSError, ValueError, LibraryError) as exc:
                # A replacement followed by an fsync error has still crossed the commit point.
                actual = self._read(identifier)
                if actual["status"] == "committed":
                    with self.lock:
                        self.records[identifier] = actual
                    self._retire_approvals(actual)
                    return actual
                if package.exists():
                    if destination.exists() or content_hash(package) != record["contentHash"]:
                        raise LibraryError(
                            "deletion_recovery_required", "Removal requires recovery", 503
                        ) from exc
                    package.rename(destination)
                    self.legacy._refresh()
                error = (
                    exc.detail
                    if isinstance(exc, LibraryError)
                    else {
                        "code": "deletion_failed",
                        "message": "Removal did not commit; original version retained",
                    }
                )
                return self._save(
                    record
                    | {"status": "blocked" if isinstance(exc, LibraryError) else "failed", "error": error}
                )
            self._retire_approvals(record)
            return record

    def request_restore(self, identifier, actor):
        with self.lock:
            record = self.show(identifier)
            if record["status"] in ("restored", "restoring"):
                return record
            if record["status"] != "committed":
                raise LibraryError("deletion_conflict", "Only a committed removal can be restored", 409)
            return self._save(
                record | {"status": "restoring", "restoredBy": actor.model_dump(), "error": None}
            )

    def restore(self, identifier):
        with self.legacy.assets._lock, import_lock(self.root):
            record = self.show(identifier)
            if record["status"] != "restoring":
                return record
            destination = self._destination(record)
            package = self.directory(identifier) / "package"
            try:
                if destination.exists() or destination.is_symlink() or destination.is_junction():
                    raise LibraryError("restore_conflict", "The original identity is already occupied", 409)
                package_files(package)
                if content_hash(package) != record["contentHash"]:
                    raise LibraryError("version_changed", "Retained package bytes have changed", 409)
                for dependency, digest in record["dependencies"].items():
                    version = self.legacy.assets.resolve(dependency)
                    self.legacy.assets.indexed_file(
                        version.root, f"assets/{version.bare_id}/{version.rev}/asset.yaml"
                    )
                    if version.content_hash != digest:
                        raise LibraryError(
                            "restore_dependency_changed", "Restore original dependencies first", 409
                        )
                destination.parent.mkdir(parents=True, exist_ok=True)
                package.rename(destination)
                self.legacy._refresh()
                return self._save(record | {"status": "restored", "retainedBytes": 0})
            except (OSError, ValueError, LibraryError) as exc:
                actual = self._read(identifier)
                if actual["status"] == "restored":
                    with self.lock:
                        self.records[identifier] = actual
                    return actual
                if destination.exists() and not package.exists():
                    if content_hash(destination) != record["contentHash"]:
                        raise LibraryError(
                            "deletion_recovery_required", "Restore requires inspection", 503
                        ) from exc
                    destination.rename(package)
                    self.legacy._refresh()
                error = (
                    exc.detail
                    if isinstance(exc, LibraryError)
                    else {
                        "code": "restore_failed",
                        "message": "Restore did not commit; recovery package retained",
                    }
                )
                return self._save(record | {"status": "committed", "error": error})

    def recover(self):
        with self.legacy.assets._lock, import_lock(self.root):
            root = destination_parent(self.root, (".trash",))
            for directory in sorted(root.iterdir()) if root.exists() else ():
                self.directory(directory.name)
                if not (directory / "operation.json").exists():
                    if any(directory.iterdir()):
                        raise ValueError("Removal journal requires inspection")
                    continue
                record = self._read(directory.name)
                original = deepcopy(record)
                destination = self._destination(record)
                package = directory / "package"
                if record["status"] in ("queued", "checking"):
                    if package.exists():
                        package_files(package)
                        if destination.exists() or content_hash(package) != record["contentHash"]:
                            raise ValueError(
                                "Interrupted removal cannot safely restore its original identity"
                            )
                        package.rename(destination)
                    record = record | {
                        "status": "failed",
                        "error": {
                            "code": "deletion_interrupted",
                            "message": "Removal interrupted; version retained",
                        },
                    }
                elif record["status"] in ("committed", "restoring"):
                    if (
                        record["status"] == "restoring"
                        and destination.exists()
                        and not package.exists()
                        and content_hash(destination) == record["contentHash"]
                    ):
                        record = record | {"status": "restored", "retainedBytes": 0}
                    elif (
                        destination.exists()
                        or not package.is_dir()
                        or content_hash(package) != record["contentHash"]
                    ):
                        raise ValueError("Retained removal bytes require inspection")
                    else:
                        record = record | {"status": "committed"}
                if record != original:
                    self._save(record)
                else:
                    self.records[record["id"]] = record
                if record["supersededApprovals"]:
                    self._retire_approvals(record)
