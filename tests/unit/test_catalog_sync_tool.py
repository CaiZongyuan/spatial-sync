"""Unit tests for the tools/sync_catalog.py lightweight catalog sync utility."""

import hashlib
import json
import shutil
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from packages.catalog.lightweight import LightweightCatalogStore
from packages.catalog_metadata.hub import HFHubReader
from packages.catalog_metadata.models import MetadataError
from packages.catalog_metadata.queries import search
from tools.sync_catalog import (
    _document_from_entry,
    _write_jsonl,
    merge_documents,
    sync_source,
    verify_catalog,
)

DATASET = "nvidia/simready-assets"
REV_A = "a" * 40
REV_B = "b" * 40
REV_BLOCKED = "f" * 40
THUMBNAIL = "1" * 64 + ".webp"


class FakeHub:
    """Loopback Hugging Face surface for one fixed-revision simready inventory."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests += 1
                path = urlparse(self.path).path
                payload = outer.routes.get(path)
                if payload is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"missing"}')
                    return
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def _package_routes(revision, packages):
    routes, root = {}, []
    for package in packages:
        name = package["name"]
        manifest = json.dumps(
            {"name": package["label"], "description": package["description"], "license": "CC-BY-4.0"},
            sort_keys=True,
        ).encode()
        root.append({"type": "directory", "path": name, "oid": package["oid"]})
        routes[f"/api/datasets/{DATASET}/tree/{revision}/{name}"] = [
            {
                "type": "file",
                "path": f"{name}/simready_usd/model.usd",
                "size": package["size"],
                "oid": "c" * 40,
            },
            {
                "type": "file",
                "path": f"{name}/simready_usd/.thumbs/256x256/model.usd.png",
                "size": 11,
                "oid": "d" * 40,
            },
            {
                "type": "file",
                "path": f"{name}/com.nvidia.simready.packaging.json",
                "size": len(manifest),
                "oid": hashlib.sha1(manifest).hexdigest(),
            },
        ]
        routes[f"/datasets/{DATASET}/resolve/{revision}/{name}/com.nvidia.simready.packaging.json"] = manifest
    routes[f"/api/datasets/{DATASET}/tree/{revision}"] = root
    return routes


PACKAGES_A = [
    {"name": "changed", "oid": "3" * 40, "size": 200, "label": "Changed object", "description": "A"},
    {"name": "kept", "oid": "1" * 40, "size": 100, "label": "Kept object", "description": "Shared"},
    {"name": "old-only", "oid": "5" * 40, "size": 300, "label": "Old only", "description": "Old description"},
]
PACKAGES_B = [
    {"name": "changed", "oid": "4" * 40, "size": 250, "label": "Changed object", "description": "B"},
    {"name": "kept", "oid": "1" * 40, "size": 100, "label": "Kept object", "description": "Shared"},
    {"name": "new-only", "oid": "2" * 40, "size": 150, "label": "New object", "description": "New"},
]


class BlockedReader:
    def enumerate(self, *args, **kwargs):
        raise MetadataError("source_request_failed", "Upstream is blocked", 502)

    def close(self):
        pass


@pytest.fixture
def isolated_catalog(tmp_path):
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    repo_catalog = Path(__file__).resolve().parents[2] / "catalog"
    shutil.copytree(repo_catalog / "data", catalog_dir / "data")
    shutil.copytree(repo_catalog / "thumbnails", catalog_dir / "thumbnails")
    return catalog_dir


@pytest.fixture
def upstream_catalog(tmp_path):
    """A versioned catalog whose seed was read from revision A of a fake upstream."""
    root = tmp_path / "upstream-catalog"
    (root / "data").mkdir(parents=True)
    (root / "thumbnails").mkdir()
    (root / "thumbnails" / THUMBNAIL).write_bytes(b"RIFF" + b"\x04\x00\x00\x00" + b"WEBP")

    hub = FakeHub(
        {
            **_package_routes(REV_A, PACKAGES_A),
            **_package_routes(REV_B, PACKAGES_B),
        }
    ).start()
    reader = HFHubReader(origin=hub.url, cache_root=root / "hub-seed")
    try:
        inventory = reader.enumerate(
            {"id": "simready", "dataset": DATASET, "recipe": "simready", "revision": REV_A}, REV_A
        )
    finally:
        reader.close()

    documents = []
    for entry in inventory["entries"]:
        document = _document_from_entry(entry)
        if document["id"] == "kept":
            document["name"]["zh"] = "保留名称"
            document["summary"]["zh"] = "保留摘要"
            document["category"] = "equipment"
            document["tags"] = ["curated"]
            document["uses"] = ["dressing"]
            document["preview"]["thumbnail"] = THUMBNAIL
        if document["id"] == "changed":
            document["name"]["zh"] = "变化名称"
            document["summary"]["zh"] = "旧摘要"
            document["category"] = "equipment"
        documents.append(document)
    documents.sort(key=lambda value: value["id"])
    _write_jsonl(root / "data" / "simready.jsonl", documents)
    (root / "data" / "sources.json").write_text(
        json.dumps(
            {
                "schema": "lw.catalog-sources/1",
                "sources": {
                    "simready": {
                        "dataset": DATASET,
                        "revision": REV_A,
                        "url": f"https://huggingface.co/datasets/{DATASET}/tree/{REV_A}",
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        yield root, hub
    finally:
        hub.stop()


def _reader(root, hub, name):
    return HFHubReader(origin=hub.url, cache_root=root / name)


def _load(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_verify_catalog_passes_on_valid_store(isolated_catalog):
    store = LightweightCatalogStore(isolated_catalog)
    assert verify_catalog(store, check_images=True) is True


def test_rebuild_install_failure_preserves_previous_database(isolated_catalog, monkeypatch):
    store = LightweightCatalogStore(isolated_catalog)
    store.ensure_db()
    assert store.db_path.is_file()

    # Simulate atomic replacement failure on Windows (e.g., PermissionError)
    original_replace = Path.replace

    def fail_replace(path, target):
        if str(path).startswith(str(store.db_path) + ".tmp."):
            raise PermissionError("simulated replacement failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(PermissionError):
        store.ensure_db(force=True)

    # Previous DB must still exist because replace was atomic and did not unlink first (E2 check)
    assert store.db_path.is_file()


def test_warm_query_does_not_rescan_data_digest(isolated_catalog, monkeypatch):
    store = LightweightCatalogStore(isolated_catalog)
    store.ensure_db()

    scans = 0
    orig_digest = store.compute_data_digest

    def counting_digest():
        nonlocal scans
        scans += 1
        return orig_digest()

    monkeypatch.setattr(store, "compute_data_digest", counting_digest)

    res = search(store, q="显微镜", limit=10)
    assert res["total"] == 8

    # Warm search must not re-compute full data digest on every entry/release read (performance check)
    assert scans == 0


def test_sync_reads_the_fixed_upstream_revision(upstream_catalog):
    root, hub = upstream_catalog
    store = LightweightCatalogStore(root)
    store.ensure_db()
    assert store.latest_release()["entryCount"] == 3
    requests_before = hub.requests

    reader = _reader(root, hub, "hub-sync")
    try:
        assert sync_source(store, "simready", REV_B, reader=reader) is True
    finally:
        reader.close()

    # Success required actual upstream reads, not a local revision relabel.
    assert hub.requests > requests_before

    documents = _load(root / "data" / "simready.jsonl")
    assert [value["id"] for value in documents] == ["changed", "kept", "new-only"]
    by_id = {value["id"]: value for value in documents}

    # Unchanged input reuses the curated summary and the existing WebP bytes.
    assert by_id["kept"]["summary"]["zh"] == "保留摘要"
    assert by_id["kept"]["category"] == "equipment"
    assert by_id["kept"]["preview"]["thumbnail"] == THUMBNAIL
    assert by_id["kept"]["preview"]["state"] == "available"

    # Changed content keeps the identity-level name but drops stale organization.
    assert by_id["changed"]["name"]["zh"] == "变化名称"
    assert by_id["changed"]["summary"]["zh"] == ""
    assert by_id["changed"]["summary"]["en"] == "B"
    assert by_id["changed"]["category"] is None
    assert "thumbnail" not in by_id["changed"]["preview"]

    assert "thumbnail" not in by_id["new-only"]["preview"]

    sources = json.loads((root / "data" / "sources.json").read_text(encoding="utf-8"))
    assert sources["sources"]["simready"]["revision"] == REV_B

    release = store.latest_release()
    assert release["entryCount"] == 3
    assert release["collections"][0]["revision"] == REV_B
    assert store.entry(f"simready/new-only@{REV_B}")["upstreamId"] == "new-only"
    with pytest.raises(MetadataError) as missing:
        store.entry(f"simready/kept@{REV_A}")
    assert missing.value.code == "unknown_entry"

    status = json.loads((root / "local" / "sync_status.json").read_text(encoding="utf-8"))["simready"]
    assert status["status"] == "success"
    assert (status["added"], status["removed"], status["changed"], status["reused"]) == (1, 1, 1, 1)
    assert status["entryCount"] == 3
    assert verify_catalog(store, check_images=True) is True
    assert verify_catalog(store, check_images=True) is True


def test_repeated_sync_is_byte_identical(upstream_catalog):
    root, hub = upstream_catalog
    store = LightweightCatalogStore(root)
    store.ensure_db()

    reader = _reader(root, hub, "hub-first")
    try:
        assert sync_source(store, "simready", REV_B, reader=reader) is True
    finally:
        reader.close()
    first_jsonl = (root / "data" / "simready.jsonl").read_bytes()
    first_sources = (root / "data" / "sources.json").read_bytes()
    first_release = store.latest_release()["id"]

    reader = _reader(root, hub, "hub-second")
    try:
        # Omitting the revision uses the manifest revision recorded by the first sync.
        assert sync_source(store, "simready", reader=reader) is True
    finally:
        reader.close()

    assert (root / "data" / "simready.jsonl").read_bytes() == first_jsonl
    assert (root / "data" / "sources.json").read_bytes() == first_sources
    assert store.latest_release()["id"] == first_release


def test_blocked_upstream_preserves_all_existing_state(upstream_catalog):
    root, _ = upstream_catalog
    store = LightweightCatalogStore(root)
    store.ensure_db()
    before_jsonl = (root / "data" / "simready.jsonl").read_bytes()
    before_sources = (root / "data" / "sources.json").read_bytes()
    before_db = store.db_path.read_bytes()
    before_release = store.latest_release()

    assert sync_source(store, "simready", REV_BLOCKED, reader=BlockedReader()) is False

    assert (root / "data" / "simready.jsonl").read_bytes() == before_jsonl
    assert (root / "data" / "sources.json").read_bytes() == before_sources
    assert store.db_path.read_bytes() == before_db
    assert store.latest_release() == before_release
    assert store.entry(f"simready/kept@{REV_A}")["upstreamId"] == "kept"

    status = json.loads((root / "local" / "sync_status.json").read_text(encoding="utf-8"))["simready"]
    assert status["status"] == "failed"
    assert "source_request_failed" in status["error"]


def test_activation_failure_restores_text_database_and_memory(upstream_catalog, monkeypatch):
    root, hub = upstream_catalog
    store = LightweightCatalogStore(root)
    store.ensure_db()
    before_jsonl = (root / "data" / "simready.jsonl").read_bytes()
    before_sources = (root / "data" / "sources.json").read_bytes()
    before_db = store.db_path.read_bytes()
    before_release = store.latest_release()

    original_replace = Path.replace

    def fail_source_replace(path, target):
        if Path(target) == store.sources_path:
            raise PermissionError("simulated activation failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_source_replace)

    reader = _reader(root, hub, "hub-failure")
    try:
        assert sync_source(store, "simready", REV_B, reader=reader) is False
    finally:
        reader.close()

    assert (root / "data" / "simready.jsonl").read_bytes() == before_jsonl
    assert (root / "data" / "sources.json").read_bytes() == before_sources
    assert store.db_path.read_bytes() == before_db
    assert store.latest_release() == before_release
    assert store.entry(f"simready/kept@{REV_A}")["upstreamId"] == "kept"
    with pytest.raises(MetadataError):
        store.entry(f"simready/new-only@{REV_B}")


def test_status_write_failure_after_commit_keeps_one_version(upstream_catalog, monkeypatch):
    root, hub = upstream_catalog
    store = LightweightCatalogStore(root)
    store.ensure_db()

    original_write_text = Path.write_text

    def fail_status_write(path, *args, **kwargs):
        if Path(path) == store.local_dir / "sync_status.json":
            raise OSError("status file is unavailable")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_status_write)

    reader = _reader(root, hub, "hub-status")
    try:
        assert sync_source(store, "simready", REV_B, reader=reader) is True
    finally:
        reader.close()

    release = store.latest_release()
    assert release["entryCount"] == 3
    assert release["collections"][0]["revision"] == REV_B
    assert "sha256:" + store.compute_data_digest() == release["id"]
    db = sqlite3.connect(store.db_path)
    try:
        assert db.execute("SELECT value FROM meta WHERE key='release_id'").fetchone()[0] == release["id"]
        assert db.execute("SELECT count(*) FROM entries").fetchone()[0] == 3
    finally:
        db.close()
    assert store.entry(f"simready/new-only@{REV_B}")["upstreamId"] == "new-only"
    with pytest.raises(MetadataError):
        store.entry(f"simready/kept@{REV_A}")


def test_running_reader_sees_replaced_database(upstream_catalog):
    root, hub = upstream_catalog
    reader_store = LightweightCatalogStore(root)
    reader_store.ensure_db()
    before = search(reader_store, q="", limit=10)
    assert before["total"] == 3
    assert before["release"] == reader_store.latest_release()["id"]

    writer_store = LightweightCatalogStore(root)
    writer_store.ensure_db()
    reader = _reader(root, hub, "hub-writer")
    try:
        assert sync_source(writer_store, "simready", REV_B, reader=reader) is True
    finally:
        reader.close()

    after = search(reader_store, q="", limit=10)
    writer_release = writer_store.latest_release()["id"]
    assert after["release"] == writer_release
    assert reader_store.latest_release()["id"] == writer_release
    assert after["total"] == 3
    assert {value["id"].split("/")[1].split("@")[0] for value in after["results"]} == {
        "changed",
        "kept",
        "new-only",
    }
    assert all(value["id"].endswith("@" + REV_B) for value in after["results"])


def test_migrated_documents_reuse_organization_by_content_digest(tmp_path):
    store = LightweightCatalogStore(tmp_path)
    previous = [{
        "id": "kept",
        "name": {"en": "Kept", "zh": "保留名称"},
        "summary": {"en": "", "zh": "旧摘要"},
        "category": "equipment",
        "tags": ["curated"],
        "contentDigest": "sha256:same",
    }]
    entry = {
        "upstreamId": "kept",
        "name": {"en": "Kept", "zh": ""},
        "summary": {"en": "Publisher text", "zh": ""},
        "contentDigest": "sha256:same",
        "facts": {},
        "preview": {},
    }
    documents, stats = merge_documents(store, previous, [entry])
    assert documents[0]["summary"]["zh"] == "旧摘要"
    assert documents[0]["category"] == "equipment"
    assert stats["reused"] == 1

    changed = {**entry, "contentDigest": "sha256:different"}
    documents, stats = merge_documents(store, previous, [changed])
    assert documents[0]["name"]["zh"] == "保留名称"
    assert documents[0]["summary"]["zh"] == ""
    assert documents[0]["category"] is None
    assert stats["changed"] == 1


def test_partial_extraction_failure_preserves_published_source(upstream_catalog):
    root, hub = upstream_catalog
    store = LightweightCatalogStore(root)
    store.ensure_db()
    before = {
        path: path.read_bytes()
        for path in (root / "data/simready.jsonl", store.sources_path, store.db_path)
    }
    release = store.latest_release()
    del hub.routes[
        f"/datasets/{DATASET}/resolve/{REV_B}/changed/com.nvidia.simready.packaging.json"
    ]
    reader = _reader(root, hub, "hub-incomplete")
    try:
        assert sync_source(store, "simready", REV_B, reader=reader) is False
    finally:
        reader.close()
    assert all(path.read_bytes() == data for path, data in before.items())
    assert store.latest_release() == release
    assert store.entry(f"simready/changed@{REV_A}")["facts"]["variants"]
    status = json.loads((store.local_dir / "sync_status.json").read_text(encoding="utf-8"))["simready"]
    assert status["status"] == "failed"
    assert "changed" in json.dumps(status["errors"])
    assert "source_extraction_failed" in status["error"]


def test_unified_search_tracks_committed_source_update(upstream_catalog, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from apps.api.app.main import create_app
    from apps.api.app.settings import Settings

    root, hub = upstream_catalog
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    store = LightweightCatalogStore(root)
    app = create_app(
        settings=Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"),
        metadata_store=store,
        metadata_launch_workers=False,
    )
    params = {"scope": "online", "view": "cards", "limit": 24}
    with TestClient(app) as client:
        before = client.get("/api/v1/library/search", params=params).json()
        assert any("old-only" in item["id"] for item in before["results"])
        reader = _reader(root, hub, "hub-unified")
        try:
            assert sync_source(LightweightCatalogStore(root), "simready", REV_B, reader=reader)
        finally:
            reader.close()
        native = client.get("/api/v1/library/catalog/maintenance/entries", params={"limit": 10}).json()
        for view in ("cards", None):
            query = {"scope": "online", "limit": 24}
            if view:
                query["view"] = view
            response = client.get("/api/v1/library/search", params=query)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["snapshot"]["id"] == native["release"] != before["snapshot"]["id"]
            assert {item["id"] for item in body["results"]} == {item["id"] for item in native["results"]}
            assert all(item["id"].endswith("@" + REV_B) for item in body["results"])
        stale = client.get("/api/v1/library/search", params={**params, "release": before["snapshot"]["id"]})
        assert stale.status_code == 409
        new_detail = client.get(f"/api/v1/library/catalog/entries/simready/new-only/{REV_B}")
        assert new_detail.status_code == 200, new_detail.text
        old_detail = client.get(f"/api/v1/library/catalog/entries/simready/old-only/{REV_A}")
        assert old_detail.status_code == 404
        old_sync = client.post("/api/v1/library/catalog/sync")
        assert old_sync.status_code == 409
        assert old_sync.json()["detail"]["code"] == "not_supported"


@pytest.mark.parametrize("abort", [False, True])
def test_readers_only_see_committed_activation(upstream_catalog, monkeypatch, abort):
    from tools import sync_catalog

    root, hub = upstream_catalog
    store = LightweightCatalogStore(root)
    writer = LightweightCatalogStore(root)
    before = search(store, q="", limit=10)
    original_replace = sync_catalog._replace_with_retry
    observations = []

    def replace_and_read(path, target, attempts=5):
        if abort and path.name == "sources.json.sync":
            raise PermissionError("abort activation after an interleaved read")
        original_replace(path, target, attempts)
        if path.name == "simready.jsonl.sync":
            observations.append(search(store, q="", limit=10))

    monkeypatch.setattr(sync_catalog, "_replace_with_retry", replace_and_read)
    reader = _reader(root, hub, "hub-activation-visibility")
    try:
        assert sync_source(writer, "simready", REV_B, reader=reader) is not abort
    finally:
        reader.close()
    assert len(observations) == 1
    assert observations[0]["release"] == before["release"]
    assert observations[0]["results"] == before["results"]
    after = search(store, q="", limit=10)
    expected_revision = REV_A if abort else REV_B
    assert all(item["id"].endswith("@" + expected_revision) for item in after["results"])
    assert (after["release"] == before["release"]) is abort


@pytest.mark.parametrize("abort", [False, True])
def test_new_reader_waits_for_complete_activation(upstream_catalog, monkeypatch, abort):
    from tools import sync_catalog

    root, hub = upstream_catalog
    writer = LightweightCatalogStore(root)
    writer.ensure_db()
    started, done = threading.Event(), threading.Event()
    observed = {}
    threads = []
    original_replace = sync_catalog._replace_with_retry

    def read_from_new_store():
        started.set()
        try:
            observed["result"] = search(LightweightCatalogStore(root), q="", limit=10)
        except Exception as exc:
            observed["error"] = repr(exc)
        finally:
            done.set()

    def replace_and_start_reader(path, target, attempts=5):
        if abort and path.name == "sources.json.sync":
            raise PermissionError("abort while a new reader waits for activation")
        original_replace(path, target, attempts)
        if path.name == "simready.jsonl.sync":
            thread = threading.Thread(target=read_from_new_store)
            threads.append(thread)
            thread.start()
            assert started.wait(2)
            assert not done.wait(0.05), "A new reader must not build the half-written source files"

    monkeypatch.setattr(sync_catalog, "_replace_with_retry", replace_and_start_reader)
    reader = _reader(root, hub, "hub-new-reader")
    try:
        assert sync_source(writer, "simready", REV_B, reader=reader) is not abort
    finally:
        reader.close()
        for thread in threads:
            thread.join(timeout=5)
    assert done.is_set()
    assert "error" not in observed, observed
    expected_revision = REV_A if abort else REV_B
    assert all(item["id"].endswith("@" + expected_revision) for item in observed["result"]["results"])


def test_sync_preserves_another_source_updated_while_fetching(upstream_catalog):
    root, hub = upstream_catalog
    sources_path = root / "data/sources.json"
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    sources["sources"]["scenario"] = {"dataset": "scenario-labs/3d-collection", "revision": REV_A}
    sources_path.write_text(json.dumps(sources), encoding="utf-8")
    _write_jsonl(root / "data/scenario.jsonl", [{"id": "other-old", "facts": {}, "preview": {}}])
    store = LightweightCatalogStore(root)
    store.ensure_db()

    class OtherSourceReader:
        def enumerate(self, profile, revision):
            return {"entries": [{"upstreamId": "other-new", "facts": {}, "preview": {}}], "complete": True}

    reader = _reader(root, hub, "hub-overlapping-fetch")
    enumerate_source = reader.enumerate

    def read_and_update_other(profile, revision):
        inventory = enumerate_source(profile, revision)
        assert sync_source(LightweightCatalogStore(root), "scenario", REV_B, reader=OtherSourceReader())
        return inventory

    reader.enumerate = read_and_update_other
    try:
        assert sync_source(store, "simready", REV_B, reader=reader)
    finally:
        reader.close()
    sources = json.loads(sources_path.read_text(encoding="utf-8"))["sources"]
    assert sources["scenario"]["revision"] == REV_B
    assert store.entry(f"scenario/other-new@{REV_B}")["upstreamId"] == "other-new"


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_source_preview_is_bounded_webp_and_available_offline(
    upstream_catalog, tmp_path, monkeypatch, image_format
):
    from fastapi.testclient import TestClient
    from PIL import Image

    from apps.api.app.main import create_app
    from apps.api.app.settings import Settings

    root, hub = upstream_catalog
    monkeypatch.setenv("LW_CATALOG_HF_ENDPOINT", hub.url)
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    store = LightweightCatalogStore(root)
    store.ensure_db()
    identity = f"simready/changed@{REV_A}"
    entry = store.entry(identity)
    source_image = BytesIO()
    Image.new("RGB", (800, 500), "#4080c0").save(source_image, format=image_format)
    route = f"/datasets/{DATASET}/resolve/{REV_A}/{entry['preview']['path']}"
    hub.routes[route] = source_image.getvalue()
    originals = {path: path.read_bytes() for path in store.data_dir.iterdir()}
    original_thumbnails = set(store.thumbnails_dir.iterdir())
    app = create_app(
        settings=Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"),
        metadata_store=store,
        metadata_launch_workers=False,
    )
    with TestClient(app) as client:
        params = {"entry": identity, "release": store.latest_release()["id"]}
        response = client.get("/api/v1/library/catalog/maintenance/preview", params=params)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/webp"
        with Image.open(BytesIO(response.content)) as image:
            image.load()
            assert image.format == "WEBP"
            assert max(image.size) == 384
        requests = hub.requests
        del hub.routes[route]
        offline = client.get("/api/v1/library/catalog/maintenance/preview", params=params)
        assert offline.status_code == 200, offline.text
        assert offline.content == response.content
        assert hub.requests == requests
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert set(store.thumbnails_dir.iterdir()) == original_thumbnails


def test_preview_urls_use_the_captured_snapshot(upstream_catalog, monkeypatch):
    from packages.catalog import lightweight

    root, hub = upstream_catalog
    store = LightweightCatalogStore(root)
    store.ensure_db()
    original_tags = lightweight._tags
    replaced = False

    def tags_after_update(document):
        nonlocal replaced
        if not replaced:
            replaced = True
            reader = _reader(root, hub, "hub-during-conversion")
            try:
                assert sync_source(LightweightCatalogStore(root), "simready", REV_B, reader=reader)
            finally:
                reader.close()
            store.latest_release()
        return original_tags(document)

    monkeypatch.setattr(lightweight, "_tags", tags_after_update)
    snapshot, entries = store.load_entries()
    assert snapshot.id != store.latest_release()["id"]
    previews = [entry.preview for entry in entries if entry.preview["kind"] == "on-demand"]
    assert previews
    assert all(parse_qs(urlparse(preview["url"]).query)["release"] == [snapshot.id] for preview in previews)
