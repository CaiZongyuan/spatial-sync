"""Exact user-version deletion: human audit, reinspection, recoverable withdrawal, then purge."""

import json
import math
import re
import shutil
from hashlib import sha256

from packages.info.approvals import ApprovalConflict, ApprovalRecord
from packages.library.archive import destination_parent, import_lock, package_files
from packages.library.common import LibraryError, content_hash
from packages.library.references import ReferenceInspection
from packages.library.reports import HASH, SUBJECT
from packages.library.transactions import durable_json, reserve_revision


class LibraryDeletions:
    def __init__(self, assets, scenes, approvals, recordings, sessions, validator, suites=None, *, live=None,
                 tasks=None, jobs=None):
        self.assets, self._scenes, self.approvals = assets, scenes, approvals
        self.root = assets.roots["user"]
        self.inspection = ReferenceInspection(assets, scenes, recordings, sessions, validator,
                                              suites=suites, live=live, tasks=tasks, jobs=jobs)

    @property
    def scenes(self):
        return self._scenes()

    def _collection(self, kind):
        return self.assets if kind == "asset" else self.scenes

    def _refresh(self):
        try:
            self.assets.startup()
            self.scenes.refresh()
        except (OSError, ValueError) as exc:
            raise LibraryError(
                "reference_inspection_incomplete", "Cannot inspect the complete library index", 409,
                complete=False, references=[],
                uninspected=[{"kind": "library", "id": "index", "reason": str(exc)}],
            ) from exc

    def _target(self, subject):
        if not isinstance(subject, str) or not SUBJECT.fullmatch(subject):
            raise LibraryError("unknown_version", "Deletion requires an exact version")
        kind, identifier = subject.split(":", 1)
        collection = self._collection(kind)
        version = collection.resolve(identifier)
        if version.root != "user":
            raise LibraryError("library_read_only", "Built-in library versions are read-only", 403)
        relative = version.path.relative_to(self.root).as_posix()
        destination_parent(self.root, tuple(relative.split("/")))
        collection.indexed_file("user", relative + f"/{kind}.yaml")
        return kind, version

    @staticmethod
    def _allowed(inspection):
        if inspection["references"]:
            raise LibraryError("version_in_use", "Version is retained by other objects", 409, **inspection)
        if not inspection["complete"]:
            raise LibraryError(
                "reference_inspection_incomplete", "Cannot establish that this version is unused", 409,
                **inspection,
            )

    def request(self, kind, identifier, actor):
        with self.assets._lock, import_lock(self.root):
            kind, version = self._target(f"{kind}:{identifier}")
            self._refresh()
            inspection = self.inspection.scan(version, kind)
            self._allowed(inspection)
            for record in self.approvals.records.values():
                if (record.subject == "deletion" and record.subjectId == f"{kind}:{version.id}"
                        and record.status == "pending"
                        and record.details.get("contentHash") == version.content_hash):
                    return record
            return self.approvals.submit(
                subject="deletion", subject_id=f"{kind}:{version.id}", requester=actor,
                title=f"删除库版本 {version.id}", details={
                    "resource": kind, "id": version.id, "root": "user",
                    "contentHash": version.content_hash, "inspection": inspection,
                    "execution": {"status": "not_started"},
                },
            )

    def _save_execution(self, record, status, **details):
        return self.approvals._save(record.model_copy(update={"details": record.details | {
            "execution": {"status": status, **details},
        }}))

    def describe(self, record):
        """A durable committed withdrawal remains visible if its final audit append failed."""
        directory = self.root / ".deletions" / record.approvalId
        path = directory / "receipt.json"
        if not path.exists():
            return record
        try:
            receipt = self._read_receipt(directory, record)
        except (OSError, ValueError) as exc:
            raise LibraryError(
                "deletion_recovery_required", "Deletion receipt requires inspection", 503,
            ) from exc
        if receipt["phase"] == "committed":
            authorization = self._authorization(receipt)
            if record.status != "approved" or record.decisions != authorization.decisions:
                raise LibraryError(
                    "deletion_recovery_required", "Approval audit requires receipt recovery", 503,
                )
            destination = self.root / receipt["relative"]
            if destination.exists() or destination.is_symlink() or destination.is_junction():
                raise LibraryError("deletion_recovery_required", "Withdrawn identity has been recreated", 503)
            execution = record.details.get("execution", {})
            if execution.get("status") != "committed":
                execution = {"status": "committed"}
            return record.model_copy(update={"details": record.details | {
                "execution": execution,
            }})
        if receipt["phase"] == "authorized" and directory.joinpath("package").exists():
            return record.model_copy(update={"details": record.details | {
                "execution": {"status": "in_progress"},
            }})
        if (receipt["phase"] == "rolled_back"
                and record.details.get("execution", {}).get("status") == "not_started"):
            return record.model_copy(update={"details": record.details | {
                "execution": {"status": "failed", "message": "Withdrawal rolled back; version retained"},
            }})
        return record

    def _receipt_directory(self, approval_id):
        if not re.fullmatch(r"approval-[a-f0-9]{32}", approval_id):
            raise ValueError("Invalid deletion receipt identity")
        return destination_parent(self.root, (".deletions", approval_id), create=True)

    def _receipt(self, directory, value, phase):
        value = value | {"phase": phase}
        durable_json(directory / "receipt.json", value)
        return value

    def _read_receipt(self, directory, record=None):
        self._receipt_directory(directory.name)
        path = directory / "receipt.json"
        if path.is_symlink() or path.is_junction():
            raise ValueError("Deletion receipt cannot be a filesystem link")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict):
            raise ValueError("Invalid deletion receipt document")
        subject, digest = receipt.get("subject"), receipt.get("contentHash")
        if (not isinstance(subject, str) or not SUBJECT.fullmatch(subject)
                or not isinstance(digest, str) or not HASH.fullmatch(digest)
                or receipt.get("approvalId") != directory.name
                or receipt.get("phase") not in ("prepared", "authorized", "rolled_back", "committed")):
            raise ValueError("Invalid deletion receipt identity or phase")
        kind, identifier = subject.split(":", 1)
        relative = ("assets/" if kind == "asset" else "scenes/") + identifier.replace("@", "/")
        if receipt.get("relative") != relative:
            raise ValueError("Deletion receipt path differs from its identity")
        if record is not None and (
            record.approvalId != directory.name or record.subject != "deletion"
            or record.subjectId != subject or record.details.get("contentHash") != digest
        ):
            raise ValueError("Deletion receipt differs from its approval")
        if receipt["phase"] in ("authorized", "committed"):
            self._authorization(receipt)
        return receipt

    def _authorization(self, receipt):
        record = ApprovalRecord.model_validate(receipt["authorization"])
        if (record.approvalId != receipt["approvalId"] or record.subject != "deletion"
                or record.subjectId != receipt["subject"] or record.status != "approved"
                or record.details.get("contentHash") != receipt["contentHash"]
                or record.details.get("root") != "user" or not record.decisions
                or record.decisions[-1].decision != "approve"
                or record.decisions[-1].actor.kind == "agent"
                or not math.isfinite(record.createdAtMs)
                or not math.isfinite(record.decisions[-1].atMs)
                or record.decisions[-1].atMs < record.createdAtMs
                or not isinstance(receipt.get("approvalStore"), str)
                or not HASH.fullmatch(receipt["approvalStore"])):
            raise ValueError("Deletion receipt has no matching human authorization")
        return record

    @property
    def approval_store(self):
        return sha256(str(self.approvals.path.resolve()).encode()).hexdigest()

    def _purge(self, directory, receipt):
        package = directory / "package"
        if package.exists():
            destination_parent(self.root, (".deletions", directory.name, "package"))
            package_files(package)
            if content_hash(package) != receipt["contentHash"]:
                raise ValueError("Quarantined package changed; cleanup requires inspection")
            shutil.rmtree(package)

    def decide(self, approval_id, decision, actor, note):
        with self.assets._lock, import_lock(self.root):
            record = self.approvals.records[approval_id]
            directory = self._receipt_directory(approval_id)
            receipt_path = directory / "receipt.json"
            receipt = self._read_receipt(directory, record) if receipt_path.exists() else None
            if receipt and receipt["phase"] == "committed":
                authorization = self._authorization(receipt)
                if record.status != "approved" or record.decisions != authorization.decisions:
                    raise ApprovalConflict("Committed deletion authorization differs from this approval")
                if decision != "approve":
                    raise ApprovalConflict("Deletion has already committed")
                return self._save_execution(record, "committed") if (
                    record.details["execution"]["status"] != "committed"
                ) else record
            if receipt and receipt["phase"] == "authorized" and directory.joinpath("package").exists():
                raise ApprovalConflict("Interrupted withdrawal must recover before another decision")
            expected = {"approve": "approved", "deny": "denied", "revoke": "revoked"}[decision]
            if record.status == expected and decision != "approve":
                return record
            if decision == "revoke":
                if record.status not in ("approved", "denied"):
                    raise ApprovalConflict("This decision cannot be revoked")
                return self.approvals.decide(approval_id, decision, actor, note)
            if record.status not in ("pending", expected):
                raise ApprovalConflict("This approval has already been processed")
            if decision == "deny":
                return self.approvals.decide(approval_id, decision, actor, note)
            try:
                kind, version = self._target(record.subjectId)
                if version.content_hash != record.details["contentHash"] or record.details["root"] != "user":
                    raise LibraryError("version_changed", "Approval target bytes or root changed", 409)
                self._refresh()
                inspection = self.inspection.scan(version, kind)
                self._allowed(inspection)
            except LibraryError as exc:
                self._save_execution(record, "blocked", error=exc.detail)
                raise
            relative = version.path.relative_to(self.root).as_posix()
            receipt = self._receipt(directory, {
                "approvalId": approval_id, "subject": record.subjectId,
                "contentHash": version.content_hash, "relative": relative,
            }, "prepared")
            # The durable human decision precedes withdrawal. Approval is not an execution result.
            if record.status == "pending":
                try:
                    record = self.approvals.decide(approval_id, "approve", actor, note)
                except OSError as exc:
                    raise LibraryError(
                        "approval_persistence_unavailable", "Human authorization could not be persisted", 503,
                    ) from exc
            receipt = self._receipt(directory, receipt | {
                "authorization": record.model_dump(mode="json"), "approvalStore": self.approval_store,
            }, "authorized")
            reserve_revision(self.root, record.subjectId.partition("@")[0], version.rev)
            snapshot = (self.assets.versions, self.assets.files, self.scenes.versions, self.scenes.files)
            package = directory / "package"
            try:
                # Recheck confinement and raw bytes immediately before the only filesystem move.
                self._target(record.subjectId)
                if package.exists() or package.is_symlink() or package.is_junction():
                    raise ValueError("Quarantine destination already exists")
                version.path.rename(package)
                package_files(package)
                if content_hash(package) != version.content_hash:
                    raise ValueError("Package bytes changed during withdrawal")
                self.assets.startup()
                self.scenes.refresh()
            except (OSError, ValueError, LibraryError) as exc:
                if package.exists():
                    if version.path.exists():
                        raise LibraryError(
                            "deletion_recovery_required", "Withdrawal requires recovery", 503,
                        ) from exc
                    package.rename(version.path)
                self.assets.versions, self.assets.files, self.scenes.versions, self.scenes.files = snapshot
                self._receipt(directory, receipt, "rolled_back")
                self._save_execution(record, "failed", message="Withdrawal rolled back; version retained")
                raise LibraryError("deletion_failed", "Deletion rolled back; version retained", 503) from exc
            # Atomic receipt replacement is the commit point. A durability error after replacement
            # must not restore a live package underneath a visible committed receipt.
            try:
                receipt = self._receipt(directory, receipt, "committed")
            except OSError as exc:
                raise LibraryError(
                    "deletion_recovery_required", "Withdrawal outcome requires receipt recovery", 503,
                ) from exc
            try:
                record = self._save_execution(record, "committed")
            except OSError as exc:
                raise LibraryError(
                    "deletion_result_unavailable",
                    "Version withdrawn; its durable receipt records completion",
                    503,
                ) from exc
            try:
                self._purge(directory, receipt)
            except (OSError, ValueError):
                record = self._save_execution(record, "committed", cleanupPending=True)
            return record

    def recover(self):
        """Before index startup: rollback uncommitted moves, finish committed receipt/audit publication."""
        with self.assets._lock, import_lock(self.root):
            root = self.root / ".deletions"
            if root.is_symlink() or root.is_junction():
                raise ValueError("Deletion receipts cannot be a filesystem link")
            for directory in sorted(root.iterdir()) if root.exists() else ():
                destination_parent(self.root, (".deletions", directory.name))
                receipt_path = directory / "receipt.json"
                if not receipt_path.exists():
                    if any(directory.iterdir()):
                        raise ValueError("Incomplete deletion receipt")
                    continue
                record = self.approvals.records.get(directory.name)
                receipt = self._read_receipt(directory, record)
                digest = receipt["contentHash"]
                destination = destination_parent(self.root, tuple(receipt["relative"].split("/")))
                package = directory / "package"
                if receipt["phase"] == "committed":
                    authorization = self._authorization(receipt)
                    if destination.exists():
                        raise ValueError("Committed deletion identity has been recreated")
                    if receipt.get("approvalStore") == self.approval_store:
                        if record is None or record.status == "pending":
                            record = self.approvals._save(authorization)
                        if record.status != "approved" or record.decisions != authorization.decisions:
                            raise ValueError("Committed deletion audit conflicts with its authorization")
                        self._save_execution(record, "committed")
                    try:
                        self._purge(directory, receipt)
                    except OSError:
                        pass  # It remains withdrawn; a future startup can retry this owned cleanup.
                elif receipt["phase"] in ("prepared", "authorized", "rolled_back"):
                    if package.exists():
                        destination_parent(self.root, (".deletions", directory.name, "package"))
                        package_files(package)
                        if destination.exists() or content_hash(package) != digest:
                            raise ValueError("Cannot safely restore withdrawn package")
                        package.rename(destination)
                    if not destination.is_dir() or content_hash(destination) != digest:
                        raise ValueError("Uncommitted deletion package is unavailable")
                    self._receipt(directory, receipt, "rolled_back")
                    if record is not None:
                        self._save_execution(record, "failed", message="Interrupted withdrawal rolled back")
                else:
                    raise ValueError("Unknown deletion transaction phase")
