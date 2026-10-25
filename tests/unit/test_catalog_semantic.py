"""Deterministic, offline semantic recall over the SF1b catalog corpus."""

import copy
import hashlib
import json
import socket
from pathlib import Path

import pytest

from packages.catalog import AssetCatalog
from packages.catalog.models import load_catalog
from packages.catalog.semantic import EmbedderInfo
from packages.catalog.views import canonical_json
from packages.library.common import LibraryError

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "catalog/simready.json"
FIXTURE = ROOT / "tests/fixtures/catalog/semantic"
CORPUS_CATALOG = FIXTURE / "catalog.json"
CORPUS_QUERIES = FIXTURE / "queries.json"
REVISION = "524747d9b5e1f21b5b18b56041788c01d745f1a1"
BOX = f"simready/box_cardboard_a01@{REVISION}"


class EmptyLibrary:
    versions = {}


class BrokenEmbedder:
    info = EmbedderInfo(
        id="broken-real-model", version="0", kind="real-cpu-multilingual", real=True,
        runtime="test", dimension=8,
    )

    def embed(self, texts, role="document"):
        raise RuntimeError("model is not available in this environment")


def catalog(path=CATALOG_PATH, **kwargs):
    value = AssetCatalog(path, **kwargs)
    value.startup()
    assert value.available, value.error
    return value


def ids(result):
    return [item["id"] for item in result["results"]]


def test_semantic_recall_is_deterministic_offline_and_labeled(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("semantic recall must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    first = catalog(CORPUS_CATALOG)
    second = catalog(CORPUS_CATALOG)
    query = "用来装实验室废弃物的容器"
    one = first.search(EmptyLibrary(), q=query, scope="online")
    two = second.search(EmptyLibrary(), q=query, scope="online")
    assert ids(one) == ids(two)
    assert one["semantic"] == two["semantic"]
    assert one["semantic"]["applied"] is True
    assert one["semantic"]["model"] == {
        "id": "lw-hash-concept-standin", "version": "1",
        "kind": "deterministic-stand-in", "real": False,
        "runtime": "numpy-hash", "dimension": 512,
    }
    assert one["semantic"]["index"]["version"].startswith("sha256:")
    assert one["semantic"]["index"]["entryCount"] == 105
    assert one["snapshot"]["id"].startswith("sha256:")
    assert one["results"]
    assert all(item["match"]["semanticScore"] > 0 for item in one["results"])
    assert one["results"][0]["match"]["semanticScore"] >= one["results"][-1]["match"]["semanticScore"]


def test_keyword_and_exact_identity_results_keep_their_existing_shape():
    value = catalog()
    keyword = value.search(EmptyLibrary(), q="纸箱", scope="online")
    assert ids(keyword) == [BOX]
    assert keyword["semantic"]["applied"] is False
    assert keyword["semantic"]["reason"] == "keyword_matches"
    assert "semanticScore" not in keyword["results"][0]["match"]

    exact = value.search(EmptyLibrary(), q=BOX, scope="online")
    assert ids(exact) == [BOX]
    assert exact["interpretedQuery"]["exact"]["type"] == "catalog"
    assert exact["semantic"]["applied"] is False
    assert exact["semantic"]["reason"] == "exact_identity"

    local = value.search(EmptyLibrary(), q="Franka", scope="local")
    assert local["semantic"]["applied"] is False
    assert local["semantic"]["reason"] == "scope_local"


def test_hard_filters_and_unknown_attributes_are_never_relaxed_by_semantics():
    value = catalog(CORPUS_CATALOG)
    blocked = value.search(
        EmptyLibrary(), q="在冰点以下存放样品的大型设备", scope="online",
        max_dimension_m=0.001,
    )
    assert blocked["semantic"]["applied"] is True
    assert blocked["semantic"]["hits"] > 0
    assert blocked["results"] == []
    assert blocked["zeroResult"]["reason"] == "hard_filters"
    assert {"filter": "maxDimensionM", "count": blocked["semantic"]["hits"]} in (
        blocked["zeroResult"]["excludedBy"]
    )

    approximate = value.search(
        EmptyLibrary(), q="在冰点以下存放样品的大型设备", scope="online",
        max_dimension_m=0.001, approximate=True,
    )
    assert approximate["results"]
    candidate = approximate["results"][0]
    assert candidate["match"]["semanticScore"] is not None
    assert "maxDimensionM" in candidate["match"]["unsatisfied"]
    assert "maxDimensionM" not in candidate["match"]["satisfied"]

    unknown = value.search(
        EmptyLibrary(), q="实验室升降台", scope="online", usage="staticCollision",
    )
    assert unknown["results"] == []
    assert unknown["zeroResult"]["reason"] == "hard_filters"
    assert "usage" in unknown["zeroResult"]["excludedBy"][0]["filter"]


def test_semantic_pages_continue_without_duplicates_and_keep_snapshot_and_index():
    value = catalog(CORPUS_CATALOG)
    query = "a container for storing lab supplies"
    first = value.search(EmptyLibrary(), q=query, scope="online", limit=3)
    assert first["semantic"]["applied"] is True
    assert first["nextCursor"]
    second = value.search(
        EmptyLibrary(), q=query, scope="online", limit=3, cursor=first["nextCursor"],
    )
    collected = ids(first) + ids(second)
    assert len(collected) == len(set(collected))
    assert first["snapshot"]["id"] == second["snapshot"]["id"]
    assert first["semantic"]["index"]["version"] == second["semantic"]["index"]["version"]
    assert first["truncated"] is True


def test_semantic_pages_stay_within_the_compact_budget():
    value = catalog(CORPUS_CATALOG)
    result = value.search(
        EmptyLibrary(), q="a container for storing lab supplies", scope="online", limit=50,
    )
    assert len(canonical_json(result).encode("utf-8")) <= 32768
    assert result["semantic"]["model"]["real"] is False


def test_real_embedder_unavailable_falls_back_to_a_labeled_standin():
    value = catalog(CORPUS_CATALOG, embedder=BrokenEmbedder())
    result = value.search(EmptyLibrary(), q="a container for storing lab supplies", scope="online")
    semantic = result["semantic"]
    assert semantic["model"]["kind"] == "deterministic-stand-in"
    assert semantic["model"]["real"] is False
    assert semantic["model"]["requested"] == "broken-real-model"
    assert "model is not available" in semantic["model"]["fallbackReason"]
    assert result["results"]


def test_corpus_is_frozen_labeled_and_measures_the_expected_contract():
    raw = CORPUS_CATALOG.read_bytes()
    snapshot, entries = load_catalog(CORPUS_CATALOG)
    corpus = json.loads(CORPUS_QUERIES.read_text(encoding="utf-8"))
    assert snapshot.id == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert corpus["catalog"]["snapshot"] == snapshot.id
    assert len(entries) == 105 == corpus["catalog"]["records"]
    assert sum(entry.package_id.startswith("fixture_") for entry in entries) == 100
    assert sum(not entry.package_id.startswith("fixture_") for entry in entries) == 5
    raw_entries = json.loads(raw)["entries"]
    real_entries = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["entries"]
    assert raw_entries[:5] == real_entries
    assert all(
        item["source"]["url"].startswith("https://example.invalid/synthetic/")
        for item in raw_entries[5:]
    )
    real = {entry.id for entry in entries if not entry.package_id.startswith("fixture_")}
    assert BOX in real
    assert corpus["labels"]["status"] == "initial"
    assert "not user-adjudicated" in corpus["labels"]["labeledBy"]
    assert len(corpus["labels"]["adjudication"]) >= 4
    queries = corpus["queries"]
    assert len(queries) == 40
    for language in ("zh", "en"):
        assert sum(query["language"] == language for query in queries) == 20
        retained = corpus["splits"]["retained"][language]
        assert len(retained) >= 8
        assert not set(retained) & set(corpus["splits"]["tuned"][language])

    value = catalog(CORPUS_CATALOG)
    filters = {
        "usage": "usage", "licenseAdmitted": "license_admitted",
        "maxDimensionM": "max_dimension_m", "minDimensionM": "min_dimension_m",
    }
    hits = {"tuned": {"zh": [0, 0], "en": [0, 0]}, "retained": {"zh": [0, 0], "en": [0, 0]}}
    exact = hard = zero = 0
    for query in queries:
        result = value.search(
            EmptyLibrary(), q=query["text"], scope="online",
            **{filters[key]: item for key, item in query["filters"].items()},
        )
        top5 = ids(result)[:5]
        if query["relevant"]:
            matched = bool(set(top5) & set(query["relevant"]))
            group = hits[query["split"]][query["language"]]
            group[0] += int(matched)
            group[1] += 1
            assert matched, query["id"]
        if query["kind"] == "exact_id":
            exact += 1
            assert top5[:1] == query["relevant"][:1]
        if query["filters"] or query["expect"] == "hard_filters":
            hard += 1
            if query["expect"] == "hard_filters":
                assert result["results"] == []
                assert result["zeroResult"]["reason"] == "hard_filters"
            else:
                assert all(
                    not item["match"]["unsatisfied"] and not item["match"]["unknown"]
                    for item in result["results"]
                )
        if query["expect"] == "zero":
            zero += 1
            assert result["results"] == []
            assert result["zeroResult"]["reason"] == "no_query_match"
    for language in ("zh", "en"):
        for split in ("tuned", "retained"):
            successes, denominator = hits[split][language]
            assert denominator == 8
            assert successes / denominator >= 0.9
    assert exact == 4 and hard == 14 and zero == 4


def test_changed_snapshot_invalidates_an_open_semantic_cursor(tmp_path):
    source = json.loads(CORPUS_CATALOG.read_text(encoding="utf-8"))
    source["indexedAt"] = "2026-10-26T00:00:00+00:00"
    changed = tmp_path / "catalog.json"
    changed.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    first = catalog(CORPUS_CATALOG).search(
        EmptyLibrary(), q="a container for storing lab supplies", scope="online", limit=2,
    )
    with pytest.raises(LibraryError) as stale:
        catalog(changed).search(
            EmptyLibrary(), q="a container for storing lab supplies", scope="online",
            limit=2, cursor=first["nextCursor"],
        )
    assert stale.value.status == 400
    assert stale.value.detail["code"] == "stale_cursor"


def test_duplicate_copy_of_a_catalog_keeps_the_same_index_version(tmp_path):
    original = catalog(CORPUS_CATALOG)
    copied = tmp_path / "catalog.json"
    copied.write_bytes(CORPUS_CATALOG.read_bytes())
    duplicate = catalog(copied)
    assert duplicate.semantic.version == original.semantic.version
    assert copy.deepcopy(original.semantic.document()) == duplicate.semantic.document()
