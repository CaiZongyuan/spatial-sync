"""Observable release/correction/recovery behavior of the local metadata core."""

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
