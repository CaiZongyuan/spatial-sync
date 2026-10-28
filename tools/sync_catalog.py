"""Synchronize and manage the lightweight repository asset catalog.

``--sync-source`` reads one collection from its pinned Hugging Face revision
through the existing source recipes, reuses applicable organization and local
thumbnails, then stages and verifies the candidate before activating the JSONL,
the sources manifest and the SQLite query cache together. ``--rebuild`` rebuilds
the local SQLite cache from the versioned files without contacting upstream.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import shutil
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.catalog.lightweight import LightweightCatalogStore
from packages.catalog.models import CatalogFormatError
from packages.catalog_metadata.hub import HFHubReader
from packages.catalog_metadata.models import DEFAULT_PROFILES, MetadataError
from packages.catalog_metadata.models import revision as parse_revision

logging.basicConfig(level=logging.INFO, format="%(message)s")
LOGGER = logging.getLogger("sync_catalog")

CONTENT_ORGANIZED_FIELDS = ("summary", "category", "tags", "domains", "uses")


def verify_catalog(store: LightweightCatalogStore, check_images: bool = True) -> bool:
    """Perform thorough verification of the versioned catalog files and cache."""
    LOGGER.info("Starting catalog verification...")
    errors = []

    # 1. Sources manifest
    if not store.sources_path.is_file():
        errors.append(f"Missing sources.json at {store.sources_path}")
        return False
    try:
        raw_sources = json.loads(store.sources_path.read_text(encoding="utf-8"))
        sources = raw_sources.get("sources", {})
        if not sources:
            errors.append("sources.json contains no sources")
    except Exception as exc:
        errors.append(f"Invalid sources.json: {exc}")
        return False

    # 2. Check JSONL files
    total_entries = 0
    coll_entries = {}
    for coll in sources:
        jsonl_path = store.data_dir / f"{coll}.jsonl"
        if not jsonl_path.is_file():
            errors.append(f"Missing JSONL file for source {coll}: {jsonl_path}")
            continue

        items = []
        line_num = 0
        with jsonl_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if "id" not in item:
                        errors.append(f"{jsonl_path.name}:{line_num}: item missing 'id'")
                    items.append(item)
                except Exception as exc:
                    errors.append(f"{jsonl_path.name}:{line_num}: invalid JSON: {exc}")

        # Check sorted by id
        ids = [item.get("id", "") for item in items]
        if ids != sorted(ids):
            errors.append(f"{jsonl_path.name}: entries are not stably sorted by id")

        coll_entries[coll] = items
        total_entries += len(items)

    # 3. Check thumbnails
    if check_images:
        missing_thumbs = 0
        corrupt_thumbs = 0
        referenced_thumbs = 0
        for coll, items in coll_entries.items():
            for item in items:
                preview = item.get("preview", {})
                thumbnail = preview.get("thumbnail")
                if thumbnail:
                    referenced_thumbs += 1
                    sha = thumbnail if isinstance(thumbnail, str) else thumbnail.get("sha256", "")
                    sha = sha.removesuffix(".webp").removeprefix("sha256:")
                    thumb_path = store.thumbnails_dir / f"{sha}.webp"
                    if not thumb_path.is_file():
                        missing_thumbs += 1
                        if missing_thumbs <= 5:
                            errors.append(f"Missing thumbnail file {sha}.webp for entry {item.get('id')}")
                    else:
                        try:
                            data = thumb_path.read_bytes()
                            if len(data) < 12 or not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
                                corrupt_thumbs += 1
                                errors.append(f"Corrupt WebP header in {thumb_path.name}")
                        except OSError as exc:
                            corrupt_thumbs += 1
                            errors.append(f"Cannot read thumbnail {thumb_path.name}: {exc}")

        LOGGER.info(
            "Thumbnails: %d referenced, %d missing, %d corrupt",
            referenced_thumbs,
            missing_thumbs,
            corrupt_thumbs,
        )

    # 4. Check SQLite rebuild and integrity
    try:
        store.ensure_db()
        with store.transaction(write=False) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                errors.append(f"SQLite integrity check failed: {integrity}")
            count = db.execute("SELECT count(*) FROM entries").fetchone()[0]
            if count != total_entries:
                errors.append(f"SQLite entry count ({count}) does not match JSONL total ({total_entries})")
    except Exception as exc:
        errors.append(f"SQLite verification error: {exc}")

    if errors:
        LOGGER.error("Verification failed with %d error(s):", len(errors))
        for err in errors[:20]:
            LOGGER.error("  - %s", err)
        if len(errors) > 20:
            LOGGER.error("  ... and %d more errors", len(errors) - 20)
        return False

    LOGGER.info("Verification passed: %d total entries across %d sources.", total_entries, len(sources))
    return True


def _recipe_for(collection_id: str) -> str | None:
    return next(
        (profile["recipe"] for profile in DEFAULT_PROFILES if profile["id"] == collection_id),
        None,
    )


def _serialize(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_jsonl(path: Path, documents: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        for document in documents:
            fp.write(_serialize(document) + "\n")


def _load_documents(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    documents = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            documents.append(json.loads(line))
    return documents


def _sha(value) -> str:
    return value.removesuffix(".webp").removeprefix("sha256:") if isinstance(value, str) else ""


def _same_content(previous: dict, entry: dict) -> bool:
    previous_digest = previous.get("contentDigest")
    return previous_digest is not None and previous_digest == entry.get("contentDigest")


def _same_input(previous: dict, entry: dict) -> bool:
    """A per-entry source fingerprint decides whether organization still applies."""
    previous_digest = previous.get("inputDigest")
    if isinstance(previous_digest, str) and previous_digest:
        return previous_digest == entry.get("inputDigest")
    # Documents migrated before inputDigest existed carry the recipe content digest.
    return _same_content(previous, entry)


def _document_from_entry(entry: dict) -> dict:
    document = {
        "id": entry["upstreamId"],
        "name": entry.get("name", {"en": "", "zh": ""}),
        "aliases": entry.get("aliases", {"en": [], "zh": []}),
        "summary": entry.get("summary", {"en": "", "zh": ""}),
        "category": entry.get("category"),
        "tags": list(entry.get("tags", [])),
        "domains": list(entry.get("domains", [])),
        "uses": list(entry.get("uses", [])),
        "facts": entry.get("facts", {}),
        "preview": copy.deepcopy(entry.get("preview", {})),
        "contentDigest": entry.get("contentDigest"),
    }
    if entry.get("inputDigest"):
        document["inputDigest"] = entry["inputDigest"]
    return document


def _reusable_thumbnail(store: LightweightCatalogStore, previous: dict, document: dict, unchanged: bool):
    """Reuse existing WebP bytes only while the source preview identity holds."""
    if not unchanged:
        return None
    old_preview = previous.get("preview") or {}
    new_preview = document.get("preview") or {}
    thumbnail = old_preview.get("thumbnail")
    if not isinstance(thumbnail, str) or not thumbnail.endswith(".webp"):
        return None
    if new_preview.get("state") in ("missing", "failed", "suspect"):
        return None
    if old_preview.get("path") != new_preview.get("path"):
        return None
    old_sha, new_sha = _sha(old_preview.get("sha256")), _sha(new_preview.get("sha256"))
    if old_sha and new_sha and old_sha != new_sha:
        return None
    if not (store.thumbnails_dir / thumbnail).is_file():
        return None
    return thumbnail


def merge_documents(
    store: LightweightCatalogStore, previous_documents: list[dict], entries: list[dict]
) -> tuple[list[dict], dict]:
    """Merge freshly read source entries with organization that still applies.

    Identity-level fields (name, aliases) survive content changes; summaries and
    classification survive only while the entry's source fingerprint is stable.
    """
    previous_by_id = {
        document["id"]: document
        for document in previous_documents
        if isinstance(document.get("id"), str)
    }
    documents: list[dict] = []
    stats = {"added": 0, "removed": 0, "changed": 0, "reused": 0}
    seen = set()
    for entry in sorted(entries, key=lambda value: value["upstreamId"]):
        upstream_id = entry["upstreamId"]
        if upstream_id in seen:
            raise MetadataError("duplicate_identity", f"Source repeated the identity {upstream_id}", 409)
        seen.add(upstream_id)
        document = _document_from_entry(entry)
        previous = previous_by_id.get(upstream_id)
        if previous is None:
            stats["added"] += 1
            documents.append(document)
            continue

        unchanged = _same_input(previous, entry)
        content_same = _same_content(previous, entry)
        for field in ("name", "aliases"):
            if isinstance(previous.get(field), dict) and previous[field]:
                document[field] = copy.deepcopy(previous[field])
        if unchanged or content_same:
            for field in CONTENT_ORGANIZED_FIELDS:
                if field in previous:
                    document[field] = copy.deepcopy(previous[field])
            stats["reused"] += 1
        else:
            stats["changed"] += 1
        thumbnail = _reusable_thumbnail(store, previous, document, unchanged)
        if thumbnail:
            preview = dict(document.get("preview") or {})
            preview["thumbnail"] = thumbnail
            preview["state"] = "available"
            document["preview"] = preview
        documents.append(document)

    stats["removed"] = len(set(previous_by_id) - seen)
    return documents, stats


def _updated_sources(raw_sources: dict, collection_id: str, target_revision: str) -> dict:
    updated = copy.deepcopy(raw_sources)
    source_info = dict(updated["sources"][collection_id])
    source_info["revision"] = target_revision
    source_info["url"] = (
        f"https://huggingface.co/datasets/{source_info['dataset']}/tree/{target_revision}"
    )
    updated["sources"][collection_id] = source_info
    return updated


def _count_entries(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as fp:
        return sum(1 for line in fp if line.strip())


def _stage_candidate(
    store: LightweightCatalogStore,
    collection_id: str,
    documents: list[dict],
    sources_document: dict,
    expected_total: int,
) -> tuple[Path, Path]:
    """Build and verify the complete candidate in an isolated directory."""
    staging = store.local_dir / f"sync-{collection_id}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    data = staging / "data"
    data.mkdir(parents=True, exist_ok=False)
    try:
        for path in sorted(store.data_dir.glob("*.jsonl")):
            if path.name != f"{collection_id}.jsonl":
                shutil.copy2(path, data / path.name)
        _write_jsonl(data / f"{collection_id}.jsonl", documents)
        (data / "sources.json").write_text(
            json.dumps(sources_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        candidate = LightweightCatalogStore(staging)
        candidate.ensure_db(force=True)
        release = candidate.latest_release()
        if release is None:
            raise CatalogFormatError("Candidate catalog did not produce a release")
        counts = {value["id"]: value["entryCount"] for value in release["collections"]}
        if counts.get(collection_id) != len(documents):
            raise CatalogFormatError(
                f"Candidate indexed {counts.get(collection_id)} {collection_id} entries, "
                f"expected {len(documents)}"
            )
        if release["entryCount"] != expected_total:
            raise CatalogFormatError(
                f"Candidate indexed {release['entryCount']} entries, expected {expected_total}"
            )
        return staging, candidate.db_path
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _replace_with_retry(source: Path, target: Path, attempts: int = 5) -> None:
    """Replace atomically, tolerating a short-lived reader handle on Windows."""
    for attempt in range(attempts):
        try:
            source.replace(target)
            return
        except OSError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.1)


def _restore(
    store: LightweightCatalogStore,
    jsonl_path: Path,
    original_jsonl: bytes,
    original_sources: bytes,
    backup: Path | None,
) -> bool:
    """Restore every effective state; returns whether the database is consistent."""
    for path, data in ((jsonl_path, original_jsonl), (store.sources_path, original_sources)):
        try:
            temporary = path.with_name(path.name + ".restore")
            temporary.write_bytes(data)
            _replace_with_retry(temporary, path)
        except OSError as exc:
            LOGGER.error("Rollback could not restore %s: %s", path, exc)
    restored = True
    try:
        if backup is not None and backup.is_file():
            _replace_with_retry(backup, store.db_path)
        elif store.db_path.is_file():
            store.db_path.unlink()
    except OSError as exc:
        restored = False
        LOGGER.error("Rollback could not restore the catalog database: %s", exc)
    try:
        store.refresh()
    except Exception as exc:
        restored = False
        LOGGER.error("Rollback could not reload catalog state: %s", exc)
    if not restored and backup is not None and backup.is_file():
        LOGGER.critical(
            "Rollback left mismatched catalog state; the previous database is retained at %s", backup
        )
    return restored


def _activate(
    store: LightweightCatalogStore,
    collection_id: str,
    documents: list[dict],
    sources_document: dict,
    staged_db: Path,
) -> None:
    """Commit data files and the query cache, or restore every effective state."""
    with store.update_lock():
        _activate_locked(store, collection_id, documents, sources_document, staged_db)


def _activate_locked(
    store: LightweightCatalogStore,
    collection_id: str,
    documents: list[dict],
    sources_document: dict,
    staged_db: Path,
) -> None:
    jsonl_path = store.data_dir / f"{collection_id}.jsonl"
    original_jsonl = jsonl_path.read_bytes()
    original_sources = store.sources_path.read_bytes()
    backup = None
    if store.db_path.is_file():
        backup = store.local_dir / "catalog.sqlite3.pre-sync"
        shutil.copy2(store.db_path, backup)
    temporary_jsonl = jsonl_path.with_name(jsonl_path.name + ".sync")
    temporary_sources = store.sources_path.with_name(store.sources_path.name + ".sync")
    committed = False
    restored = True
    try:
        _write_jsonl(temporary_jsonl, documents)
        _replace_with_retry(temporary_jsonl, jsonl_path)
        temporary_sources.write_text(
            json.dumps(sources_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _replace_with_retry(temporary_sources, store.sources_path)
        _replace_with_retry(staged_db, store.db_path)
        committed = True
    except Exception:
        restored = _restore(store, jsonl_path, original_jsonl, original_sources, backup)
        raise
    finally:
        for path in (temporary_jsonl, temporary_sources):
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
        if backup is not None and backup.is_file() and (committed or restored):
            try:
                backup.unlink()
            except OSError:
                pass
    try:
        # Status and in-memory state are auxiliary: the files and cache are committed.
        store.refresh()
    except Exception as exc:
        LOGGER.warning("Catalog state could not be reloaded after activation: %s", exc)


def _write_status(store: LightweightCatalogStore, collection_id: str, record: dict) -> None:
    """Best-effort progress file; an activated catalog is valid without it."""
    path = store.local_dir / "sync_status.json"
    all_status = {}
    if path.is_file():
        try:
            all_status = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            all_status = {}
    all_status[collection_id] = record
    try:
        store.local_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(all_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        LOGGER.warning("Sync status could not be written to %s: %s", path, exc)


def _error_text(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    return f"{code}: {message}" if code and message else str(exc)


def _status_record(collection_id: str, target_revision: str | None, **fields) -> dict:
    record = {
        "collection": collection_id,
        "revision": target_revision,
        "lastSync": datetime.now(UTC).isoformat(),
        "status": "failed",
        "error": None,
    }
    record.update(fields)
    return record


def sync_source(
    store: LightweightCatalogStore,
    collection_id: str,
    new_revision: str | None = None,
    *,
    reader: HFHubReader | None = None,
    budgets: dict | None = None,
) -> bool:
    """Read one source at its fixed revision and activate verified candidates.

    Success requires a completed upstream read; this function never derives a new
    revision from the existing local files. Failure before the commit point
    restores the JSONL, the sources manifest and the SQLite cache together.
    """
    store.local_dir.mkdir(parents=True, exist_ok=True)
    if not store.sources_path.is_file():
        LOGGER.error("Missing sources manifest: %s", store.sources_path)
        return False
    try:
        raw_sources = json.loads(store.sources_path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.error("Invalid sources manifest %s: %s", store.sources_path, exc)
        return False
    sources = raw_sources.get("sources", {})
    if collection_id not in sources:
        LOGGER.error("Unknown source collection: %s", collection_id)
        return False
    source_info = sources[collection_id]
    recipe = _recipe_for(collection_id)
    if recipe is None:
        LOGGER.error("No source recipe is registered for collection '%s'", collection_id)
        _write_status(
            store,
            collection_id,
            _status_record(collection_id, None, error="No source recipe is registered for this collection"),
        )
        return False
    try:
        target_revision = parse_revision(new_revision or source_info.get("revision"))
    except MetadataError as exc:
        LOGGER.error("Sync failed for '%s': %s", collection_id, exc)
        _write_status(store, collection_id, _status_record(collection_id, None, error=str(exc)))
        return False

    LOGGER.info("Reading source '%s' at revision %s...", collection_id, target_revision[:10])
    profile = {
        "id": collection_id,
        "dataset": source_info["dataset"],
        "recipe": recipe,
        "enabled": True,
        "revision": target_revision,
    }
    owned_reader = reader is None
    if reader is None:
        reader = HFHubReader(cache_root=store.local_dir / "hub-cache", budgets=budgets)
    try:
        inventory = reader.enumerate(profile, target_revision)
    except Exception as exc:
        LOGGER.error(
            "Sync failed for '%s': %s", collection_id, exc, exc_info=not isinstance(exc, MetadataError)
        )
        _write_status(
            store,
            collection_id,
            _status_record(collection_id, target_revision, error=_error_text(exc)),
        )
        return False
    finally:
        if owned_reader:
            reader.close()

    entries = inventory.get("entries") or []
    errors = inventory.get("errors") or [
        {"upstreamId": entry.get("upstreamId"), **(entry.get("extractionError") or {})}
        for entry in entries
        if entry.get("extractionError") or entry.get("extractionStatus") == "failed"
    ]
    if errors:
        message = f"source_extraction_failed: {len(errors)} entries failed; current source retained"
        LOGGER.error("Sync failed for '%s': %s", collection_id, message)
        _write_status(
            store,
            collection_id,
            _status_record(collection_id, target_revision, error=message, errors=errors),
        )
        return False
    if not entries:
        LOGGER.error("Sync failed for '%s': upstream inventory was empty", collection_id)
        _write_status(
            store,
            collection_id,
            _status_record(collection_id, target_revision, error="Upstream inventory was empty"),
        )
        return False

    staging = None
    try:
        with store.update_lock():
            # Network reads can overlap, but merge and activation use the latest
            # committed inputs so one source cannot overwrite another's update.
            current_sources = json.loads(store.sources_path.read_text(encoding="utf-8"))
            if current_sources.get("sources", {}).get(collection_id) != source_info:
                raise MetadataError(
                    "source_changed", "Source configuration changed; retry synchronization", 409
                )
            previous_documents = _load_documents(store.data_dir / f"{collection_id}.jsonl")
            documents, stats = merge_documents(store, previous_documents, entries)
            sources_document = _updated_sources(current_sources, collection_id, target_revision)
            expected_total = len(documents) + sum(
                _count_entries(store.data_dir / f"{name}.jsonl")
                for name in current_sources["sources"]
                if name != collection_id
            )
            staging, staged_db = _stage_candidate(
                store, collection_id, documents, sources_document, expected_total
            )
            _activate(store, collection_id, documents, sources_document, staged_db)
    except Exception as exc:
        LOGGER.error("Sync failed for '%s': %s", collection_id, exc)
        _write_status(
            store,
            collection_id,
            _status_record(collection_id, target_revision, error=_error_text(exc)),
        )
        return False
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)

    usage = inventory.get("usage") or {}
    _write_status(
        store,
        collection_id,
        _status_record(
            collection_id,
            target_revision,
            status="success",
            entryCount=len(documents),
            added=stats["added"],
            removed=stats["removed"],
            changed=stats["changed"],
            reused=stats["reused"],
            requests=usage.get("requests"),
            networkBytes=usage.get("networkBytes"),
        ),
    )
    LOGGER.info(
        "Sync successful for '%s': %d entries at revision %s (added %d, removed %d, changed %d, reused %d)",
        collection_id,
        len(documents),
        target_revision[:10],
        stats["added"],
        stats["removed"],
        stats["changed"],
        stats["reused"],
    )
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Synchronize or rebuild the lightweight catalog SQLite query cache."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Catalog root directory (defaults to repo catalog/)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild the local SQLite cache from catalog/data without contacting upstream",
    )
    parser.add_argument(
        "--check-thumbnails",
        action="store_true",
        help="Check that all referenced thumbnails exist",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify all catalog data, line counts, JSON validity and SQLite integrity",
    )
    parser.add_argument(
        "--sync-source",
        type=str,
        default=None,
        help="Read one source collection from its pinned upstream revision and activate it",
    )
    parser.add_argument(
        "--revision",
        type=str,
        default=None,
        help="Fixed 40-character revision for --sync-source (defaults to the manifest revision)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print the last recorded sync status and coverage metrics",
    )
    args = parser.parse_args()

    store = LightweightCatalogStore(args.root or REPO_ROOT / "catalog")
    if not store.is_available_at(store.root):
        LOGGER.error("Catalog data not found at %s", store.data_dir)
        sys.exit(1)

    if args.verify:
        if args.rebuild:
            store.ensure_db(force=True)
        success = verify_catalog(store, check_images=args.check_thumbnails)
        sys.exit(0 if success else 1)

    if args.sync_source:
        success = sync_source(store, args.sync_source, args.revision)
        sys.exit(0 if success else 1)

    db_path = store.ensure_db(force=args.rebuild)
    release = store.latest_release()
    collections = store.collections()

    LOGGER.info("Lightweight Catalog Summary:")
    LOGGER.info("  Catalog Root:     %s", store.root)
    LOGGER.info("  SQLite Cache:     %s", db_path)
    LOGGER.info("  Release ID:       %s", release["id"])
    LOGGER.info("  Built At:         %s", release["createdAt"])
    LOGGER.info("  Total Entries:    %d", release["entryCount"])
    LOGGER.info("  Collections:      %d", len(collections))
    for c in collections:
        rev_prefix = c["revision"][:10]
        LOGGER.info(
            "    - %-10s %5d entries (%s @ %s)",
            c["id"],
            c["entryCount"],
            c["dataset"],
            rev_prefix,
        )

    if args.status:
        status_path = store.local_dir / "sync_status.json"
        if status_path.is_file():
            LOGGER.info("Last sync status:")
            LOGGER.info("%s", status_path.read_text(encoding="utf-8").rstrip())
        else:
            LOGGER.info("Last sync status: none")

    if args.check_thumbnails:
        snapshot, entries = store.load_entries()
        total_thumbs = 0
        missing_thumbs = 0
        for e in entries:
            if e.preview and e.preview.get("kind") == "local":
                total_thumbs += 1
                url = e.preview.get("url")
                sha = url.split("sha256:")[1].split(".webp")[0]
                try:
                    store.thumbnail(sha)
                except Exception as exc:
                    LOGGER.warning("Thumbnail check failed for %s (%s): %s", e.id, sha, exc)
                    missing_thumbs += 1

        LOGGER.info("Thumbnails Check:")
        LOGGER.info("  Referenced WebP:  %d", total_thumbs)
        LOGGER.info("  Verified OK:      %d", total_thumbs - missing_thumbs)
        LOGGER.info("  Missing/Corrupt:  %d", missing_thumbs)
        if missing_thumbs > 0:
            sys.exit(2)


if __name__ == "__main__":
    main()
