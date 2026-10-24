"""Budget estimates, durable reservations and cross-process ledger locking.

One gateway process owns the ledger, but the file lock keeps a second process
from silently overspending if one is started by mistake. Charges survive the
attempt that created them so retry and restart never reset used batch budget;
only reservations are released.

A reservation holds the cache and free-disk space an active attempt needs until
it finishes. Every check counts the other active reservations as well, so two
concurrent starts cannot both pass preflight and then overspend. A reservation
records ``sourceBytes + adaptedUpperBytes`` as its disk hold; the free-disk
reserve itself is a floor checked by :meth:`preflight`, not space an attempt
consumes.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from packages.acquisition import cache
from packages.acquisition.models import BUDGET_SCHEMA
from packages.library.common import LibraryError
from packages.library.transactions import durable_json


@contextlib.contextmanager
def ledger_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / (path.name + ".lock")
    if lock_path.is_symlink() or lock_path.is_junction():
        raise ValueError("Budget ledger lock cannot be a filesystem link")
    with lock_path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


class BudgetExceeded(Exception):
    """A reservation failed; ``reasons`` name every limit that would be exceeded."""

    def __init__(self, reasons):
        self.reasons = tuple(str(reason) for reason in reasons)
        super().__init__(" ".join(self.reasons))


def _bytes(value):
    """Ledger values are internal, but a malformed number never becomes a discount."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


class BudgetLedger:
    def __init__(self, path, limits, *, cache_root, disk_usage):
        self.path = Path(path)
        self.limits = limits
        self.cache_root = Path(cache_root)
        self.disk_usage = disk_usage

    def _read(self):
        if not self.path.exists():
            return {"schema": BUDGET_SCHEMA, "charges": {}, "reservations": {}}
        try:
            if self.path.is_symlink() or self.path.is_junction():
                raise ValueError("Budget ledger cannot be a filesystem link")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("schema") != BUDGET_SCHEMA
                or not isinstance(data.get("charges"), dict)
                or not isinstance(data.get("reservations"), dict)
            ):
                raise ValueError("Invalid budget ledger")
            return data
        except (OSError, ValueError) as exc:
            raise LibraryError(
                "library_unavailable", "The acquisition budget ledger is unreadable", 503
            ) from exc

    def _write(self, data):
        durable_json(self.path, data)

    def charges(self):
        return self._read()["charges"]

    def reservations(self):
        return self._read()["reservations"]

    def _reasons(self, ledger, source_bytes, adapted_upper, *, record_id, active):
        limits = self.limits
        reasons = []
        if source_bytes > limits.source_package_bytes:
            reasons.append(
                f"Source package is {source_bytes} bytes; the per-acquisition limit is "
                f"{limits.source_package_bytes}."
            )
        if adapted_upper > limits.adapted_package_bytes:
            reasons.append(
                f"Adapted package upper bound is {adapted_upper} bytes; the limit is "
                f"{limits.adapted_package_bytes}."
            )
        used = sum(
            _bytes(charge.get("sourceBytes"))
            for identifier, charge in ledger["charges"].items()
            if identifier != record_id and isinstance(charge, dict)
        )
        if used + source_bytes > limits.batch_bytes:
            reasons.append(
                f"Batch source bytes would be {used + source_bytes}; the batch limit is "
                f"{limits.batch_bytes}."
            )
        other = [
            reservation
            for identifier, reservation in ledger["reservations"].items()
            if identifier != record_id and isinstance(reservation, dict)
        ]
        reserved_source = sum(_bytes(item.get("sourceBytes")) for item in other)
        current = cache.total_bytes(self.cache_root)
        reclaimable = cache.reclaimable(self.cache_root, set(active))
        after = current - reclaimable
        if after + source_bytes + reserved_source > limits.raw_cache_bytes:
            reasons.append(
                f"Raw cache after LRU cleanup and other active reservations would be "
                f"{after + source_bytes + reserved_source} bytes; the cache limit is "
                f"{limits.raw_cache_bytes}."
            )
        reserved_disk = sum(_bytes(item.get("reservedDiskBytes")) for item in other)
        consumed = source_bytes + adapted_upper
        if self.disk_usage() - (consumed + reserved_disk) < limits.free_disk_min_bytes:
            reasons.append(
                f"Free disk after this acquisition's {consumed} bytes and {reserved_disk} "
                f"reserved bytes would leave less than {limits.free_disk_min_bytes} bytes."
            )
        return reasons

    def preflight(self, entry, *, record_id=None, active=()):
        source_bytes = entry.source.bytes
        adapted_known = entry.adaptation.output_bytes
        adapted_upper = (
            adapted_known if adapted_known is not None else self.limits.adapted_package_bytes
        )
        reused = min(
            cache.cached_bytes(
                self.cache_root, entry.provider, entry.revision, entry.package_id
            ),
            source_bytes,
        )
        estimate = {
            "source": {
                "bytes": source_bytes,
                "files": entry.source.files,
                "licenseFiles": [entry.license.evidence.file],
                "bomItems": entry.source.bom_items,
                "bomNotInBom": list(entry.source.bom_not_in_bom),
            },
            "cache": {
                "reusedBytes": reused,
                "known": True,
                "reason": "The raw cache is inspected before acquisition starts.",
            },
            "transfer": {"upperBoundBytes": max(0, source_bytes - reused)},
            "temporary": {"upperBoundBytes": source_bytes},
            "adaptedPackage": {
                "bytes": adapted_known,
                "upperBoundBytes": adapted_upper,
                "known": adapted_known is not None,
            },
            "requiredDiskBytes": source_bytes + adapted_upper + self.limits.free_disk_min_bytes,
            "limits": self.limits.document(),
            "withinLimits": True,
            "license": {
                "admitted": entry.license.admitted,
                "reasons": list(entry.license.obligations) if entry.license.admitted else [],
            },
            "usage": {name: entry.usage_value(name) for name in ("dressing", "staticCollision")},
            "acquisition": entry.acquisition.document(),
            "unknown": entry.unknown_attributes(),
            "networkRequired": True,
            "estimated": True,
        }
        reasons = self._reasons(
            self._read(),
            source_bytes,
            adapted_upper,
            record_id=record_id,
            active=active,
        )
        estimate["withinLimits"] = not reasons
        return estimate, tuple(reasons)

    def retry_reasons(self, record, *, active=()):
        source_bytes = record.estimate.get("source", {}).get("bytes", 0)
        adapted_upper = record.estimate.get("adaptedPackage", {}).get("upperBoundBytes") or (
            self.limits.adapted_package_bytes
        )
        return tuple(
            self._reasons(
                self._read(),
                source_bytes,
                adapted_upper,
                record_id=record.id,
                active=active,
            )
        )

    def reserve(self, *, record_id, attempt, source_bytes, adapted_upper, active=()):
        """Hold the source, adapted and disk space for one attempt.

        The checks and the write share the ledger lock, and every check counts
        other active reservations; the same record's previous charge and
        reservation are excluded so a retry keeps its charge without
        double-counting it.
        """
        reserved_disk = source_bytes + adapted_upper
        with ledger_lock(self.path):
            ledger = self._read()
            reasons = self._reasons(
                ledger,
                source_bytes,
                adapted_upper,
                record_id=record_id,
                active=active,
            )
            if reasons:
                raise BudgetExceeded(reasons)
            used = sum(
                _bytes(charge.get("sourceBytes"))
                for identifier, charge in ledger["charges"].items()
                if identifier != record_id and isinstance(charge, dict)
            )
            previous = ledger["charges"].get(record_id)
            charge = max(
                _bytes(previous.get("sourceBytes")) if isinstance(previous, dict) else 0,
                source_bytes,
            )
            ledger["charges"][record_id] = {"sourceBytes": charge}
            reservation = {
                "id": f"{record_id}@{attempt}",
                "sourceBytes": source_bytes,
                "adaptedUpperBytes": adapted_upper,
                "reservedDiskBytes": reserved_disk,
                "batchBytes": used + source_bytes,
            }
            ledger["reservations"][record_id] = reservation
            self._write(ledger)
            return {
                "reservationId": reservation["id"],
                "sourceBytes": source_bytes,
                "adaptedUpperBytes": adapted_upper,
                "reservedDiskBytes": reserved_disk,
                "batchBytes": reservation["batchBytes"],
                "releasedAt": None,
            }

    def release(self, record_id, *, release_charge=False):
        released = datetime.now(UTC).isoformat()
        with ledger_lock(self.path):
            ledger = self._read()
            reservation = ledger["reservations"].pop(record_id, None)
            charge = ledger["charges"].pop(record_id, None) if release_charge else None
            if reservation is not None or charge is not None:
                self._write(ledger)
        return released
