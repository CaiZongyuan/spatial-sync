"""Observable release/correction/recovery behavior of the local metadata core."""

import threading
from copy import deepcopy

import pytest

from packages.catalog_metadata import MetadataError, MetadataStore

REV_A = "a" * 40
REV_B = "b" * 40


def item(identifier="beaker", **fields):
    return {"upstreamId": identifier, "name": {"en": "Beaker", "zh": "烧杯"},
            "facts": {"dimensionsM": None, "sourceBytes": None}, **fields}


def stage(store, revision=REV_A, entries=None, complete=True):
    return store.stage_inventory("simready", revision, entries or [item()], complete=complete)


def test_complete_release_partial_failure_and_exact_history_survive_restart(tmp_path):
    store = MetadataStore(tmp_path)
    first = stage(store)
    release = store.publish([first["id"]])
    old_identity = store.entries()[0]["id"]
    partial = stage(store, REV_B, [item("bucket")], complete=False)
    with pytest.raises(MetadataError, match="Incomplete enumeration"):
        store.publish([partial["id"]])
    store = MetadataStore(tmp_path)
    assert store.latest_release()["id"] == release["id"]
    assert store.entry(old_identity)["facts"]["dimensionsM"] is None
    second = stage(store, REV_B, [item("bucket")])
    latest = store.publish([second["id"]])
    assert latest["id"] != release["id"]
    assert store.entries(release["id"])[0]["id"] == old_identity
    assert store.entry(old_identity)["revision"] == REV_A


def test_index_failure_and_unchanged_publication_are_atomic(tmp_path):
    store = MetadataStore(tmp_path)
    first = stage(store)
    release = store.publish([first["id"]])
    second = stage(store, REV_B)

    def broken_index(entries):
        assert entries[0]["revision"] == REV_B
        raise RuntimeError("index unavailable")

    with pytest.raises(RuntimeError, match="index unavailable"):
        store.publish([second["id"]], index_builder=broken_index)
    assert store.latest_release() == release
    assert store.publish([first["id"]]) == release


def test_manual_correction_rejects_stale_batch_and_remains_after_reorganization(tmp_path):
    store = MetadataStore(tmp_path)
    inventory = stage(store)
    before = store.pending(inventory["id"])[0]
    correction = store.correct("simready", "beaker", {"name": {"zh": "人工名称"}},
                               base_version=0, reason="Reviewed the source")
    with pytest.raises(MetadataError, match="Manual correction changed"):
        store.submit_organization(inventory["id"], "beaker", {"name": {"zh": "模型名称"}},
                                  input_digest=before["inputDigest"], correction_version=0)
    entry = store.submit_organization(inventory["id"], "beaker", {"name": {"zh": "模型名称"}},
                                      input_digest=before["inputDigest"], correction_version=1)
    assert entry["name"]["zh"] == "人工名称"
    first = store.publish([inventory["id"]])
    store.retract(correction["id"], base_version=1)
    second = store.publish([inventory["id"]])
    assert store.entries(first["id"])[0]["name"]["zh"] == "人工名称"
    assert store.entries(second["id"])[0]["name"]["zh"] == "模型名称"


def test_submission_replay_after_restart_is_idempotent_and_reuse_is_by_input(tmp_path):
    store = MetadataStore(tmp_path)
    inventory = stage(store)
    before = store.pending(inventory["id"])[0]
    arguments = {"input_digest": before["inputDigest"], "correction_version": 0,
                 "versions": {"skill": "maintenance/1", "recipe": "hf/1"},
                 "submission_key": "run-1:batch-1:beaker"}
    entry = store.submit_organization(inventory["id"], "beaker", {"tags": ["laboratory"]}, **arguments)
    store = MetadataStore(tmp_path)
    assert store.submit_organization(inventory["id"], "beaker", {"tags": ["laboratory"]}, **arguments) == entry
    assert entry["organization"]["version"] == 1
    with pytest.raises(MetadataError, match="different data"):
        store.submit_organization(inventory["id"], "beaker", {"tags": ["wrong"]}, **arguments)
    second = stage(store, REV_B)
    assert store.pending(second["id"]) == []
    changed = stage(store, "c" * 40, [item(contentDigest="changed")])
    assert len(store.pending(changed["id"])) == 1


def test_organization_cannot_invent_technical_facts_and_export_restores_history(tmp_path):
    store = MetadataStore(tmp_path / "first")
    inventory = stage(store)
    before = store.pending(inventory["id"])[0]
    with pytest.raises(MetadataError, match="descriptive fields only"):
        store.submit_organization(inventory["id"], "beaker", {"facts": {"dimensionsM": [1, 2, 3]}},
                                  input_digest=before["inputDigest"])
    evidence = store.put_blob(b"source text", origin="publisher")
    store.correct("simready", "beaker", {"tags": ["reviewed"]}, reason="Human review")
    release = store.publish([inventory["id"]])
    restored = MetadataStore(tmp_path / "restored")
    restored.restore(store.export())
    assert restored.latest_release() == release
    assert restored.entries() == store.entries()
    assert restored.blob(evidence["sha256"]) == b"source text"


def test_composed_release_retains_other_collection_and_reconciles_counts(tmp_path):
    store = MetadataStore(tmp_path)
    one = stage(store)
    two = store.stage_inventory("chemlab", REV_A, [item("instrument")], complete=True,
                                counts={"entries": 1, "expectedEntries": 1, "files": 4, "variants": 2})
    old = store.publish([one["id"], two["id"]])
    replacement = stage(store, REV_B)
    new = store.publish([replacement["id"]])
    assert len(new["collections"]) == 2
    assert new["entryCount"] == old["entryCount"] == 2
    with pytest.raises(MetadataError, match="reconcile"):
        store.stage_inventory("chemlab", REV_B, [item()], complete=True, counts={"expectedEntries": 265})


def test_readers_observe_old_release_during_publication_transaction(tmp_path):
    store = MetadataStore(tmp_path)
    inventory = stage(store)
    old = store.publish([inventory["id"]])
    replacement = stage(store, REV_B)
    building, finish = threading.Event(), threading.Event()
    failures = []

    def index_builder(entries):
        building.set()
        if not finish.wait(5):
            raise AssertionError("Reader was blocked by publication")

    def publish():
        try:
            store.publish([replacement["id"]], index_builder=index_builder)
        except BaseException as exc:
            failures.append(exc)

    publisher = threading.Thread(target=publish)
    publisher.start()
    assert building.wait(5)
    try:
        assert store.latest_release()["id"] == old["id"]
        assert store.entries()[0]["revision"] == REV_A
    finally:
        finish.set()
        publisher.join(5)
    assert not publisher.is_alive()
    assert failures == []


def test_changed_preview_tags_and_evidence_change_source_input(tmp_path):
    store = MetadataStore(tmp_path)
    first = stage(store, entries=[item(preview={"path": "normal.png", "role": "texture"})])
    before = store.inventory_entries(first["id"])[0]
    second = stage(store, entries=[item(tags=["publisher-tag"], preview={"path": "object.png", "role": "object"})])
    after = store.inventory_entries(second["id"])[0]
    assert first["id"] != second["id"]
    assert before["inputDigest"] != after["inputDigest"]
    assert after["preview"]["path"] == "object.png"
    assert after["tags"] == ["publisher-tag"]


def test_dataset_alias_registration_is_not_an_independent_collection(tmp_path):
    store = MetadataStore(tmp_path)
    with pytest.raises(MetadataError, match="already has a collection"):
        store.register_collection({"id": "duplicate", "dataset": "nvidia/simready-assets", "recipe": "simready"})


def test_organization_selection_can_be_retracted_without_losing_result_history(tmp_path):
    store = MetadataStore(tmp_path)
    inventory = stage(store)
    before = store.pending(inventory["id"])[0]
    args = {"input_digest": before["inputDigest"]}
    store.submit_organization(inventory["id"], "beaker", {"tags": ["first"]}, **args)
    store.submit_organization(inventory["id"], "beaker", {"tags": ["second"]}, base_version=1, **args)
    old = store.publish([inventory["id"]])
    store.select_organization("simready", "beaker", before["inputDigest"], 1, base_version=2)
    selected = store.inventory_entry(inventory["id"], "beaker")
    assert selected["tags"] == ["first"]
    assert selected["organization"]["version"] == 3
    store.select_organization("simready", "beaker", before["inputDigest"], 0, base_version=3)
    assert len(store.pending(inventory["id"])) == 1
    assert len(store.organization_history("simready", "beaker", before["inputDigest"])) == 2
    assert store.entries(old["id"])[0]["tags"] == ["second"]
    with pytest.raises(MetadataError, match="selection changed"):
        store.select_organization("simready", "beaker", before["inputDigest"], 1, base_version=3)


def test_restore_write_failure_and_tampered_release_leave_active_data_unchanged(tmp_path, monkeypatch):
    original = MetadataStore(tmp_path / "original")
    inv = stage(original)
    release = original.publish([inv["id"]])
    backup_store = MetadataStore(tmp_path / "source")
    replacement = stage(backup_store, REV_B)
    backup_store.publish([replacement["id"]])
    backup_store.put_blob(b"new evidence")
    backup = backup_store.export()

    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(original, "put_blob", disk_full)
        with pytest.raises(OSError, match="disk full"):
            original.restore(backup)
    assert original.latest_release() == release
    tampered = deepcopy(backup)
    row = tampered["tables"]["release_entries"][0]
    row["search_text"] = "tampered index"
    with pytest.raises(MetadataError, match="index differs"):
        original.restore(tampered)
    assert original.latest_release() == release
