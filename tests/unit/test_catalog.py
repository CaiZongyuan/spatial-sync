"""The committed catalog, unified search, bounded pages, describe and estimate."""

import base64
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.catalog import AssetCatalog, load_catalog
from packages.catalog.models import ESTIMATE_SCHEMA, SEARCH_SCHEMA
from packages.catalog.views import canonical_json, serialized_size
from packages.library.common import LibraryError

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "catalog/simready.json"
SUPPORT_PATH = ROOT / "factory/support.json"
REVISION = "524747d9b5e1f21b5b18b56041788c01d745f1a1"


def catalog(path=CATALOG_PATH):
    value = AssetCatalog(path)
    value.startup()
    assert value.available, value.error
    return value


class EmptyLibrary:
    versions = {}

    def resolve(self, selector):
        raise LibraryError("unknown_version", "Unknown asset version")


class FakeVersion:
    def __init__(self, identifier, revision, path, metadata, provenance=None,
                 content_hash=None, root="user", report=None):
        self.id = identifier
        self.rev = revision
        self.path = Path(path)
        self.metadata = metadata
        self.content_hash = content_hash or hashlib.sha256(identifier.encode()).hexdigest()
        self.root = root
        self.provenance = provenance
        self._report = report
        self.path.mkdir(parents=True, exist_ok=True)
        if provenance is not None:
            (self.path / "provenance.json").write_text(
                json.dumps(provenance), encoding="utf-8"
            )

    @property
    def bare_id(self):
        return self.id.partition("@")[0]

    def report(self):
        return self._report

    def summary(self):
        return {
            "id": self.id,
            "assetId": self.bare_id,
            "assetVersion": self.id,
            "rev": self.rev,
            "kind": self.metadata["kind"],
            "title": self.metadata["title"],
            "dimensionsM": self.metadata.get("dimensionsM"),
            "implementedTypes": [],
            "gaps": ["visual"],
            "gapCount": 1,
            "completeness": {"status": "warning", "gaps": ["visual"], "items": []},
            "validationStatus": "not_recorded",
            "thumbnailUrl": None,
            "visualUrl": None,
        }


class FakeLibrary:
    def __init__(self, versions):
        self.versions = {version.id: version for version in versions}

    def resolve(self, selector):
        bare, separator, revision = selector.partition("@")
        matches = [
            version for version in self.versions.values()
            if version.bare_id == bare
            and (not separator or version.rev == int(revision))
        ]
        if not matches:
            raise LibraryError("unknown_version", "Unknown asset version")
        return max(matches, key=lambda version: version.rev)


def make_version(tmp_path, name="fixture", revision=1, *, metadata=None, provenance=None,
                 root="user", report=None):
    metadata = metadata or {
        "id": f"environment/{name}",
        "kind": "environment",
        "title": name.replace("-", " ").title(),
        "description": "Local fixture",
        "dimensionsM": [0.5, 0.4, 0.3],
        "dressing": True,
    }
    return FakeVersion(
        f"environment/{name}@{revision}", revision, tmp_path / name / str(revision),
        metadata, provenance=provenance, root=root, report=report,
    )


def checks(statuses, *, collision=None):
    return {
        "checks": [
            {"id": identifier, "tier": "structure", "status": status}
            for identifier, status in statuses.items()
        ],
        "physics": {"status": "completed" if "P1" in statuses else "skipped"},
    } | ({"collision": collision} if collision is not None else {})


def raw_entry():
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(next(entry for entry in data["entries"]
                              if entry["packageId"] == "box_cardboard_a01"))


def write_catalog(tmp_path, entries, *, indexed_at="2026-10-24T13:53:52+00:00",
                  filename="catalog.json"):
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    data["indexedAt"] = indexed_at
    data["entries"] = entries
    path = tmp_path / filename
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def ids(result):
    return [item["id"] for item in result["results"]]


def package(result):
    return [item["id"].split("/")[1].partition("@")[0] for item in result["results"]]


def canonical_bytes(value):
    return len(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8"))


def test_catalog_facts_match_factory_support_and_committed_bytes():
    value = catalog()
    raw = CATALOG_PATH.read_bytes()
    raw_data = json.loads(raw)
    assert set(raw_data) == {
        "schema", "provider", "dataset", "revision", "indexedAt", "coverage", "entries",
    }
    support = json.loads(SUPPORT_PATH.read_text(encoding="utf-8"))
    assert value.snapshot.id == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert value.snapshot.indexed_at.isoformat() == "2026-10-24T13:53:52+00:00"
    assert value.snapshot.indexed_at.isoformat() == support["checkedAt"]
    assert value.snapshot.entry_count == 5 == len(value.entries)
    assert value.snapshot.coverage == "Curated SimReady subset; not the complete dataset."
    assert value.snapshot.limits == {
        key: support["limits"][key] for key in (
            "sourcePackageBytes", "adaptedPackageBytes", "batchBytes", "rawCacheBytes",
            "freeDiskMinBytes", "concurrentAcquisitions",
        )
    }
    assert {entry.package_id for entry in value.entries} == {
        item["package"] for item in support["sourcePackages"]
    }
    for source in support["sourcePackages"]:
        entry = value.by_package[source["package"]]
        assert entry.revision == source["revision"] == REVISION
        assert entry.kind == "environment"
        assert entry.source.url == source["source"]["url"]
        assert entry.source.files == source["source"]["files"]
        assert entry.source.bytes == source["source"]["bytes"]
        assert entry.source.root_usd == source["source"]["rootUsd"]
        assert entry.source.bom_items == source["source"]["bomItems"]
        assert list(entry.source.bom_not_in_bom) == source["source"]["bomNotInBom"]
        assert entry.license.name == source["license"]["name"]
        assert entry.license.admitted is True
        assert list(entry.license.obligations) == source["license"]["obligations"]
        assert entry.license.in_bom is source["license"]["inBom"] is False
        assert entry.license.evidence.file == source["license"]["file"]
        assert entry.license.evidence.sha256 == source["license"]["sha256"]
        assert [item.file for item in entry.license.attachments] == [
            item["file"] for item in source["mdl"]["licenseEvidence"]
        ]
        assert all(item.license == "BSD-3-Clause" for item in entry.license.attachments)
        adaptation = source["adaptation"]
        assert entry.adaptation.status == adaptation["status"]
        assert list(entry.adaptation.gaps) == adaptation["gaps"]
        assert (
            list(entry.adaptation.dimensions_m)
            if entry.adaptation.dimensions_m is not None else None
        ) == adaptation["dimensionsM"]
        output = adaptation["output"] or {}
        assert entry.adaptation.output_bytes == output.get("bytes")
        assert entry.adaptation.output_files == output.get("files")
        assert entry.adaptation.variant == adaptation["variant"]["selected"]
        assert list(entry.adaptation.approximations) == adaptation["approximations"]
        upstream = source["upstreamValidation"]
        assert entry.upstream_validation.profile == upstream["profile"]
        assert entry.upstream_validation.profile_version == upstream["profileVersion"]
        assert entry.upstream_validation.date == upstream["date"]
        assert list(entry.upstream_validation.failing_features) == upstream["failingFeatures"]
        assert entry.upstream_validation.declared_by == upstream["declaredBy"]
        for name, key in (("dressing", "dressingUsable"),
                          ("staticCollision", "staticCollisionUsable")):
            expected = source["usage"][key]
            expected = None if expected == "unknown" else expected
            assert entry.usage[name].eligible is expected
            assert entry.usage[name].unknown is (expected is None)
        assert entry.acquisition.eligible is (source["import"]["status"] == "pass")
        if not entry.acquisition.eligible:
            assert entry.acquisition.reasons
            reason = entry.acquisition.reasons[0]
            assert (
                (source["import"]["reason"] and source["import"]["reason"] in reason)
                or any(gap in reason for gap in source["adaptation"]["gaps"])
            )


def test_catalog_limits_and_upstream_declaration_are_optional_overrides(tmp_path):
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    data.pop("limits", None)
    data["entries"][0]["upstreamValidation"].pop("declaredBy")
    path = tmp_path / "minimal.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    value = catalog(path)
    support = json.loads(SUPPORT_PATH.read_text(encoding="utf-8"))
    assert value.snapshot.limits == {
        key: support["limits"][key] for key in (
            "sourcePackageBytes", "adaptedPackageBytes", "batchBytes", "rawCacheBytes",
            "freeDiskMinBytes", "concurrentAcquisitions",
        )
    }
    entry = value.describe("simready", "box_cardboard_a01", REVISION)
    assert entry["upstreamValidation"]["declaredBy"] == "not recorded"


def test_catalog_bilingual_content_and_aliases_are_exact():
    value = catalog()
    expected = {
        "box_cardboard_a01": ("纸箱", {"瓦楞纸箱", "纸盒", "包装箱"}),
        "bucket_utility_gray_a01": ("灰色工具桶", {"水桶", "提桶", "工具桶"}),
        "container_recycle_square_blue_a02": ("蓝色方形回收箱", {"回收桶", "垃圾桶", "废料箱"}),
        "equipment_utility_cart_e01": ("工具推车", {"台车", "推车", "工具车"}),
        "desk_a01": ("实验室工作台", {"工作台", "实验台", "桌子"}),
    }
    for package_id, (name, aliases) in expected.items():
        entry = value.by_package[package_id]
        assert entry.names["zh"] == name
        assert set(entry.aliases["zh"]) == aliases
        assert entry.names["en"].strip()
        assert entry.aliases["en"]


def test_exact_catalog_id_and_ref_return_the_online_identity():
    value = catalog()
    entry = value.by_package["box_cardboard_a01"]
    for query in (entry.id, f"catalog:{entry.id}"):
        result = value.search(EmptyLibrary(), q=query, scope="online")
        assert result["interpretedQuery"]["exact"] == {
            "type": "catalog", "id": entry.id, "ref": entry.ref,
        }
        assert ids(result) == [entry.id]
        assert result["results"][0]["ref"] == f"catalog:{entry.id}"
        assert result["results"][0]["type"] == "online"
        assert result["counts"] == {
            "returned": 1, "matching": 1, "localMatching": 0, "onlineMatching": 1,
            "approximate": 0, "budgetTruncated": False,
        }


def test_exact_local_id_uses_the_supplied_revision_and_the_latest_without_revision(tmp_path):
    first = make_version(tmp_path, "cardboard-box", 1, provenance={
        "sourceRevision": REVISION, "sourcePackage": "box_cardboard_a01",
    })
    second = make_version(tmp_path, "cardboard-box", 2, provenance={
        "sourceRevision": REVISION, "sourcePackage": "box_cardboard_a01",
    })
    library = FakeLibrary([first, second])
    value = catalog()
    exact = value.search(library, q="environment/cardboard-box@1", scope="local")
    assert ids(exact) == ["environment/cardboard-box@1"]
    latest = value.search(library, q="environment/cardboard-box", scope="local")
    assert ids(latest) == ["environment/cardboard-box@2"]
    assert latest["interpretedQuery"]["exact"]["type"] == "local"


def test_unknown_exact_identities_explain_the_zero_result():
    value = catalog()
    result = value.search(EmptyLibrary(), q=f"simready/absent@{REVISION}", scope="online")
    assert result["zeroResult"]["reason"] == "unknown_exact_id"
    assert result["results"] == []
    result = value.search(EmptyLibrary(), q="environment/absent", scope="local")
    assert result["zeroResult"]["reason"] == "unknown_exact_id"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("纸箱", "box_cardboard_a01"),
        ("瓦楞纸箱", "box_cardboard_a01"),
        ("carton", "box_cardboard_a01"),
        ("cardboard carton", "box_cardboard_a01"),
        ("灰色工具桶", "bucket_utility_gray_a01"),
        ("水桶", "bucket_utility_gray_a01"),
        ("pail", "bucket_utility_gray_a01"),
        ("蓝色方形回收箱", "container_recycle_square_blue_a02"),
        ("回收桶", "container_recycle_square_blue_a02"),
        ("waste bin", "container_recycle_square_blue_a02"),
        ("工具推车", "equipment_utility_cart_e01"),
        ("台车", "equipment_utility_cart_e01"),
        ("push cart", "equipment_utility_cart_e01"),
        ("实验室工作台", "desk_a01"),
        ("实验台", "desk_a01"),
        ("workbench", "desk_a01"),
    ],
)
def test_bilingual_names_and_aliases_match_with_reasons(query, expected):
    result = catalog().search(EmptyLibrary(), q=query, scope="online")
    assert package(result) == [expected]
    assert result["results"][0]["title"] == result["results"][0]["names"][
        "zh" if any("\u4e00" <= character <= "\u9fff" for character in query) else "en"
    ]
    assert result["results"][0]["match"]["reasons"]


def test_latin_keywords_match_words_not_accidental_substrings():
    value = catalog()
    cart = value.search(EmptyLibrary(), q="cart", scope="online")
    assert package(cart) == ["equipment_utility_cart_e01"]
    plural = value.search(EmptyLibrary(), q="bins", scope="online")
    assert package(plural) == ["container_recycle_square_blue_a02"]
    partial = value.search(EmptyLibrary(), q="card", scope="online")
    assert partial["results"] == []


def test_scope_dedup_and_cross_scope_explanations(tmp_path):
    correlated = make_version(tmp_path, "cardboard-box", 1, provenance={
        "sourceRevision": REVISION, "sourcePackage": "box_cardboard_a01",
    })
    unrelated = make_version(tmp_path, "lab-crate", 1)
    library = FakeLibrary([correlated, unrelated])
    value = catalog()
    online = value.search(library, q="纸箱", scope="online")
    assert ids(online) == [value.by_package["box_cardboard_a01"].id]
    assert online["results"][0]["installed"]["versions"] == [{
        "id": "environment/cardboard-box@1",
        "contentHash": correlated.content_hash,
        "usage": {"dressing": None, "staticCollision": None},
        "license": {"name": None, "admitted": False, "unknown": True},
        "unknown": ["license", "dressing", "staticCollision"],
    }]
    local = value.search(library, q="zzz", scope="local")
    assert local["zeroResult"]["reason"] == "no_query_match"
    local = value.search(library, q="workbench", scope="local")
    assert local["zeroResult"]["reason"] == "scope"
    assert local["zeroResult"]["suggestions"] == [{"action": "broaden_scope"}]
    combined = value.search(library, q="environment", scope="all")
    combined_ids = ids(combined)
    assert value.by_package["box_cardboard_a01"].id in combined_ids
    assert "environment/cardboard-box@1" not in combined_ids
    assert "environment/lab-crate@1" in combined_ids
    local_only = value.search(library, q="environment", scope="local")
    assert {"environment/cardboard-box@1", "environment/lab-crate@1"}.issubset(ids(local_only))


def test_provenance_correlation_requires_exact_revision_and_package(tmp_path):
    wrong_revision = make_version(tmp_path, "box-rev", 1, provenance={
        "sourceRevision": "0" * 40, "sourcePackage": "box_cardboard_a01",
    })
    wrong_package = make_version(tmp_path, "box-package", 1, provenance={
        "sourceRevision": REVISION, "sourcePackage": "absent_package",
    })
    library = FakeLibrary([wrong_revision, wrong_package])
    value = catalog()
    result = value.search(library, q="box", scope="online")
    assert result["results"][0]["installed"] == {
        "versions": [], "latest": None, "truncated": False,
    }
    combined = value.search(library, q="environment", scope="all")
    assert {"environment/box-rev@1", "environment/box-package@1"} == {
        item for item in ids(combined) if item.startswith("environment/")
    }


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"kind": "environment"}, {
            "box_cardboard_a01", "bucket_utility_gray_a01",
            "container_recycle_square_blue_a02", "equipment_utility_cart_e01", "desk_a01",
        }),
        ({"kind": "robot"}, set()),
        ({"provider": "simready"}, {
            "box_cardboard_a01", "bucket_utility_gray_a01",
            "container_recycle_square_blue_a02", "equipment_utility_cart_e01", "desk_a01",
        }),
        ({"provider": "other"}, set()),
        ({"usage": "staticCollision"}, {"box_cardboard_a01", "bucket_utility_gray_a01"}),
        ({"usage": "dressing"}, {
            "box_cardboard_a01", "bucket_utility_gray_a01", "container_recycle_square_blue_a02",
        }),
        ({"license_admitted": True}, {
            "box_cardboard_a01", "bucket_utility_gray_a01",
            "container_recycle_square_blue_a02", "equipment_utility_cart_e01", "desk_a01",
        }),
        ({"license_admitted": False}, set()),
        ({"min_source_bytes": 20_000_000}, {
            "box_cardboard_a01", "equipment_utility_cart_e01", "desk_a01",
        }),
        ({"max_source_bytes": 16_000_000}, {
            "bucket_utility_gray_a01", "container_recycle_square_blue_a02",
        }),
        ({"min_dimension_m": 1.0}, {"desk_a01"}),
        ({"max_dimension_m": 0.9}, {
            "box_cardboard_a01", "bucket_utility_gray_a01", "container_recycle_square_blue_a02",
        }),
    ],
)
def test_hard_filters_exclude_unknown_and_failing_candidates(filters, expected):
    result = catalog().search(EmptyLibrary(), q="environment", scope="online", **filters)
    assert set(package(result)) == expected
    expected_applied = {
        name: value for name, value in {
            "kind": filters.get("kind"), "provider": filters.get("provider"),
            "usage": filters.get("usage"),
            "licenseAdmitted": filters.get("license_admitted"),
            "minSourceBytes": filters.get("min_source_bytes"),
            "maxSourceBytes": filters.get("max_source_bytes"),
            "minDimensionM": filters.get("min_dimension_m"),
            "maxDimensionM": filters.get("max_dimension_m"),
        }.items() if value is not None
    }
    assert result["appliedFilters"] == expected_applied
    assert result["unknownPolicy"] == "excluded"


def test_filters_that_exclude_everything_explain_each_filter_and_offer_actions():
    result = catalog().search(EmptyLibrary(), q="environment", scope="online", max_dimension_m=0.1)
    assert result["results"] == []
    zero = result["zeroResult"]
    assert zero["reason"] == "hard_filters"
    assert {"filter": "maxDimensionM", "count": 5} in zero["excludedBy"]
    assert {"action": "remove_filter", "filter": "maxDimensionM"} in zero["suggestions"]
    assert {"action": "enable_approximation"} in zero["suggestions"]


def test_approximate_results_are_marked_and_come_after_exact_matches():
    result = catalog().search(EmptyLibrary(), q="environment", scope="online",
                              usage="dressing", approximate=True)
    exact = result["results"][:3]
    assert {item["id"] for item in exact} == {
        "simready/box_cardboard_a01@" + REVISION,
        "simready/bucket_utility_gray_a01@" + REVISION,
        "simready/container_recycle_square_blue_a02@" + REVISION,
    }
    assert all(not item["match"]["unsatisfied"] and not item["match"]["unknown"]
               for item in exact)
    assert result["counts"]["approximate"] == 2
    approximate = result["results"][3:]
    assert {item["id"] for item in approximate} == {
        "simready/equipment_utility_cart_e01@" + REVISION,
        "simready/desk_a01@" + REVISION,
    }
    assert all(item["match"]["unknown"] == ["usage"] for item in approximate)


def test_exact_identity_is_never_relaxed_by_approximation():
    value = catalog()
    entry = value.by_package["box_cardboard_a01"]
    result = value.search(EmptyLibrary(), q=entry.id, scope="online", kind="robot",
                          approximate=True)
    assert result["results"] == []
    assert result["zeroResult"]["reason"] == "hard_filters"
    assert any("Exact ID conditions are not relaxed" in note
               for note in result["interpretedQuery"]["notes"])


def test_installed_filter_tracks_provenance_correlation(tmp_path):
    version = make_version(tmp_path, "cardboard-box", 1, provenance={
        "sourceRevision": REVISION, "sourcePackage": "box_cardboard_a01",
    })
    library = FakeLibrary([version])
    value = catalog()
    installed = value.search(library, q="environment", scope="online", installed=True)
    assert package(installed) == ["box_cardboard_a01"]
    assert installed["results"][0]["installed"]["latest"] == "environment/cardboard-box@1"
    not_installed = value.search(library, q="environment", scope="online", installed=False)
    assert "box_cardboard_a01" not in package(not_installed)
    assert len(not_installed["results"]) == 4


def test_local_results_use_their_own_license_and_validation_not_catalog_claims(tmp_path):
    provenance = {
        "sourceRevision": REVISION,
        "sourcePackage": "box_cardboard_a01",
        "license": {"name": "CC-BY-4.0", "obligations": ["attribution"]},
    }
    passing_metadata = {
        "id": "environment/cardboard-box", "kind": "environment", "title": "Cardboard Box",
        "description": "Local fixture", "dimensionsM": [0.7, 0.5, 0.5],
        "physics": {"collision": "authored"},
    }
    passing = make_version(
        tmp_path, "cardboard-box", 1, metadata=passing_metadata, provenance=provenance,
        report=checks({"S1": "pass", "S2": "pass", "S4": "pass", "S6": "pass",
                       "P1": "pass", "P3": "pass"}),
    )
    failing = make_version(
        tmp_path, "cardboard-box", 2, metadata=passing_metadata, provenance=provenance,
        report=checks({"S1": "pass", "S2": "pass", "S4": "pass", "S6": "pass",
                       "P1": "fail", "P3": "pass"}),
    )
    unvalidated = make_version(
        tmp_path, "cardboard-box", 3, metadata=passing_metadata, provenance=provenance,
    )
    unlicensed = make_version(tmp_path, "mystery-crate", 1, metadata={
        "id": "environment/mystery-crate", "kind": "environment", "title": "Mystery crate",
        "description": "No provenance", "dimensionsM": [0.2, 0.2, 0.2],
    })
    for version in (passing, failing, unvalidated):
        (version.path / "LICENSE.txt").write_text("CC-BY-4.0", encoding="utf-8")
    library = FakeLibrary([passing, failing, unvalidated, unlicensed])
    value = catalog()
    result = value.search(library, q="environment", scope="local")
    by_id = {item["id"]: item for item in result["results"]}
    box = by_id["environment/cardboard-box@1"]
    assert box["license"]["admitted"] is True
    assert box["usage"]["dressing"] is True and box["usage"]["staticCollision"] is True
    assert box["source"]["provider"] == "simready"
    assert box["source"]["bytes"] == 21530693
    assert box["local"]["asset"]["title"] == "Cardboard Box"
    assert box["installed"]["latest"] == "environment/cardboard-box@3"
    assert box["installed"]["truncated"] is False
    assert box["installed"]["versions"][1]["id"] == "environment/cardboard-box@2"
    assert box["installed"]["versions"][1]["usage"]["staticCollision"] is False
    combined = value.search(library, q="纸箱", scope="all")
    assert [item["id"] for item in combined["results"]] == [
        "simready/box_cardboard_a01@" + REVISION
    ]
    online = combined["results"][0]
    assert online["usage"]["staticCollision"] is True
    assert online["installed"]["versions"][1]["usage"]["staticCollision"] is False
    # A failing local P1 must not inherit the catalog's passing static-collision claim.
    assert by_id["environment/cardboard-box@2"]["usage"] == {
        "dressing": True, "staticCollision": False,
        "evidence": (
            "Derived from this version's recorded checks: S1/S2/S4/S6 for dressing and "
            "P1/P3 plus an authored collision for static collision. Missing checks stay unknown."
        ),
    }
    assert by_id["environment/cardboard-box@3"]["usage"]["dressing"] is None
    assert by_id["environment/cardboard-box@3"]["usage"]["staticCollision"] is None
    mystery = by_id["environment/mystery-crate@1"]
    assert mystery["license"] == {
        "name": None, "admitted": False, "unknown": True, "obligations": [],
    }
    assert mystery["usage"]["dressing"] is None
    assert mystery["usage"]["staticCollision"] is None
    assert {"dressing", "staticCollision"}.issubset(mystery["unknown"])
    assert mystery["acquisition"]["eligible"] is False


def test_cursor_pages_are_stable_and_limit_may_change(tmp_path):
    value = catalog()
    first = value.search(EmptyLibrary(), q="environment", scope="online", sort="name", limit=1)
    assert first["counts"]["returned"] == 1 and first["truncated"] is True
    assert first["nextCursor"]
    second = value.search(EmptyLibrary(), q="environment", scope="online", sort="name",
                          limit=4, cursor=first["nextCursor"])
    collected = ids(first) + ids(second)
    assert len(collected) == 5 == len(set(collected))
    assert second["truncated"] is False and second["nextCursor"] is None
    with pytest.raises(LibraryError) as mismatch:
        value.search(EmptyLibrary(), q="纸箱", scope="online", sort="name",
                     limit=4, cursor=first["nextCursor"])
    assert mismatch.value.status == 400
    assert mismatch.value.detail["code"] == "cursor_mismatch"


def test_cursor_rejects_invalid_payloads_and_stale_snapshots(tmp_path):
    value = catalog()
    with pytest.raises(LibraryError) as invalid:
        value.search(EmptyLibrary(), q="environment", scope="online", cursor="not-a-cursor")
    assert invalid.value.detail["code"] == "invalid_cursor"
    first = value.search(EmptyLibrary(), q="environment", scope="online", limit=1)
    other = catalog(write_catalog(tmp_path, json.loads(CATALOG_PATH.read_text(encoding="utf-8"))[
        "entries"
    ], indexed_at="2026-10-25T00:00:00+00:00"))
    with pytest.raises(LibraryError) as stale:
        other.search(EmptyLibrary(), q="environment", scope="online", limit=1,
                     cursor=first["nextCursor"])
    assert stale.value.status == 400
    assert stale.value.detail["code"] == "stale_cursor"


@pytest.mark.parametrize("limit", [0, 51, True, "10"])
def test_invalid_limit_is_a_bounded_error(limit):
    with pytest.raises(LibraryError) as invalid:
        catalog().search(EmptyLibrary(), q="环境", scope="online", limit=limit)
    assert invalid.value.status == 400
    assert invalid.value.detail["code"] == "invalid_limit"


def test_compact_pages_stay_under_32_kib_and_continue_without_skipping(tmp_path):
    base = raw_entry()
    entries = []
    for index in range(40):
        entry = copy.deepcopy(base)
        entry["packageId"] = f"widget_{index:02d}"
        entry["source"]["url"] = (
            "https://huggingface.co/datasets/nvidia/simready-assets/tree/"
            f"{REVISION}/widget_{index:02d}"
        )
        entry["source"]["bytes"] = 100000 + index
        entry["source"]["files"] = 10 + index
        entry["names"] = {"en": f"Widget {index:02d} " + "x" * 280,
                          "zh": f"部件{index:02d}" + "长" * 250}
        entry["aliases"] = {"en": ["alias " + "y" * 180], "zh": ["别名" + "长" * 180]}
        entry["descriptions"] = {"en": "long description " * 230,
                                 "zh": "很长的描述" * 600}
        entries.append(entry)
    value = catalog(write_catalog(tmp_path, entries))
    collected, cursor, pages = [], None, 0
    while True:
        result = value.search(EmptyLibrary(), q="widget", scope="online", limit=50,
                              cursor=cursor)
        assert canonical_bytes(result) <= 32768
        assert result["schema"] == SEARCH_SCHEMA
        collected.extend(ids(result))
        pages += 1
        if result["nextCursor"] is None:
            assert result["truncated"] is False
            break
        assert result["truncated"] is True
        cursor = result["nextCursor"]
    assert pages > 1
    assert len(collected) == 40 == len(set(collected))
    assert set(collected) == {f"simready/widget_{index:02d}@{REVISION}" for index in range(40)}


def test_zero_and_exact_results_stay_within_budget_for_adversarial_queries():
    value = catalog()
    queries = (
        "9" * 40_000 + "m",
        "simready/" + "a" * 40_000 + "@" + "0" * 40,
        "x" * 40_000,
    )
    for query in queries:
        result = value.search(EmptyLibrary(), q=query, scope="online")
        assert canonical_bytes(result) <= 32768, query[:20]
        assert result["zeroResult"] is not None
        assert result["results"] == []


def test_oversized_local_provenance_fields_are_bounded_and_reachable(tmp_path):
    version = make_version(tmp_path, "big-thing", 1, provenance={
        "sourcePackage": "P" * 50_000,
        "sourceRevision": "R" * 50_000,
        "license": {"name": "L" * 50_000, "file": "LICENSE.txt"},
    })
    (version.path / "LICENSE.txt").write_text("fixture", encoding="utf-8")
    library = FakeLibrary([version])
    value = catalog()
    result = value.search(library, q="big", scope="local")
    assert canonical_bytes(result) <= 32768
    assert ids(result) == ["environment/big-thing@1"]
    assert len(result["results"][0]["license"]["name"]) <= 200
    assert len(result["results"][0]["source"]["packageId"]) <= 200
    exact = value.search(library, q="environment/big-thing", scope="local")
    assert canonical_bytes(exact) <= 32768
    assert ids(exact) == ["environment/big-thing@1"]


def test_cursor_offset_past_the_match_list_is_invalid(tmp_path):
    value = catalog()
    first = value.search(EmptyLibrary(), q="environment", scope="online", limit=2)
    payload = json.loads(base64.urlsafe_b64decode(
        first["nextCursor"] + "=" * (-len(first["nextCursor"]) % 4)
    ))
    payload["offset"] = 999
    cursor = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(LibraryError) as invalid:
        value.search(EmptyLibrary(), q="environment", scope="online", cursor=cursor)
    assert invalid.value.status == 400
    assert invalid.value.detail["code"] == "invalid_cursor"


def test_cursor_binds_filters_and_sort(tmp_path):
    value = catalog()
    first = value.search(EmptyLibrary(), q="environment", scope="online",
                         kind="environment", limit=1)
    with pytest.raises(LibraryError) as filters:
        value.search(EmptyLibrary(), q="environment", scope="online", kind="robot",
                     cursor=first["nextCursor"])
    assert filters.value.detail["code"] == "cursor_mismatch"
    with pytest.raises(LibraryError) as sort:
        value.search(EmptyLibrary(), q="environment", scope="online", kind="environment",
                     sort="name", cursor=first["nextCursor"])
    assert sort.value.detail["code"] == "cursor_mismatch"


def test_local_unknown_usage_and_license_are_excluded_by_hard_filters(tmp_path):
    version = make_version(tmp_path, "mystery-crate", 1, metadata={
        "id": "environment/mystery-crate", "kind": "environment", "title": "Mystery crate",
        "description": "No provenance or checks", "dimensionsM": [0.2, 0.2, 0.2],
    })
    library = FakeLibrary([version])
    value = catalog()
    for filters in ({"license_admitted": True}, {"usage": "dressing"}):
        result = value.search(library, q="mystery", scope="local", **filters)
        assert result["results"] == []
        assert result["zeroResult"]["reason"] == "hard_filters"
    approximate = value.search(library, q="mystery", scope="local", usage="dressing",
                               approximate=True)
    assert ids(approximate) == ["environment/mystery-crate@1"]
    assert "usage" in approximate["results"][0]["match"]["unknown"]


def test_local_license_requires_retained_evidence(tmp_path):
    bare = make_version(tmp_path, "bare-license", 1, provenance={
        "license": {"name": "CC-BY-4.0"},
    })
    backed = make_version(tmp_path, "backed-license", 1, provenance={
        "license": {"name": "CC-BY-4.0"},
    })
    (backed.path / "LICENSE.txt").write_text("CC-BY-4.0", encoding="utf-8")
    result = catalog().search(FakeLibrary([bare, backed]), q="license", scope="local")
    by_id = {item["id"]: item for item in result["results"]}
    assert by_id["environment/bare-license@1"]["license"]["unknown"] is True
    assert by_id["environment/backed-license@1"]["license"]["admitted"] is True


def test_empty_query_browses_and_snapshot_reports_offline_freshness():
    now = datetime(2026, 10, 24, 14, 30, 0, tzinfo=UTC)
    result = catalog().search(EmptyLibrary(), scope="online", now=now)
    snapshot = result["snapshot"]
    assert snapshot["id"].startswith("sha256:")
    assert snapshot["provider"] == "simready"
    assert snapshot["providerRevision"] == REVISION
    assert snapshot["indexedAt"] == "2026-10-24T13:53:52+00:00"
    assert snapshot["entryCount"] == 5
    assert snapshot["coverage"] == "Curated SimReady subset; not the complete dataset."
    assert snapshot["offlineReadable"] is True
    assert snapshot["networkRequired"] is False
    assert snapshot["ageSeconds"] == int((now - datetime(2026, 10, 24, 13, 53, 52,
                                                          tzinfo=UTC)).total_seconds())
    assert result["counts"]["returned"] == 5


def test_describe_summary_full_digest_and_unknown_entry():
    value = catalog()
    entry = value.by_package["box_cardboard_a01"]
    summary = value.describe("simready", entry.package_id, REVISION, library=EmptyLibrary())
    assert summary["schema"] == "lw.catalog-entry/1"
    assert summary["view"] == "summary"
    assert serialized_size(summary) <= 8192
    assert summary["names"] == {"en": "Cardboard box", "zh": "纸箱"}
    assert summary["aliases"]["zh"] == ["瓦楞纸箱", "纸盒", "包装箱"]
    assert summary["installed"] == {"versions": [], "latest": None, "truncated": False}
    assert summary["unknown"] == []
    assert summary["details"]["view"] == "full"
    assert summary["details"]["documentDigest"] == summary["documentDigest"]
    assert summary["estimate"]["tool"] == "catalog_estimate"
    full = value.describe("simready", entry.package_id, REVISION, view="full",
                          document_digest=summary["documentDigest"])
    assert full["view"] == "full"
    assert serialized_size(full) <= 32768
    assert full["documentDigest"] == summary["documentDigest"]
    digest_input = {key: item for key, item in full.items()
                    if key not in ("view", "documentDigest", "previewTruncated")}
    assert hashlib.sha256(canonical_json(digest_input).encode()).hexdigest() == (
        summary["documentDigest"]
    )
    with pytest.raises(LibraryError) as changed:
        value.describe("simready", entry.package_id, REVISION,
                       document_digest="0" * 64)
    assert changed.value.status == 409
    assert changed.value.detail["code"] == "catalog_changed"
    with pytest.raises(LibraryError) as unknown:
        value.describe("simready", "absent", REVISION)
    assert unknown.value.status == 404
    assert unknown.value.detail["code"] == "unknown_catalog_entry"


def test_maximal_valid_entry_describe_stays_bounded(tmp_path):
    entry = raw_entry()
    entry["names"] = {"en": "N" * 20_000, "zh": "名" * 20_000}
    entry["aliases"] = {"en": ["A" * 20_000] * 10, "zh": ["别" * 20_000] * 10}
    entry["descriptions"] = {"en": "D" * 200_000, "zh": "描" * 200_000}
    entry["usage"]["dressing"]["evidence"] = "E" * 100_000
    entry["source"]["bomNotInBom"] = ["B" * 10_000] * 100
    entry["license"]["obligations"] = ["O" * 10_000] * 20
    value = catalog(write_catalog(tmp_path, [entry]))
    summary = value.describe("simready", "box_cardboard_a01", REVISION)
    full = value.describe("simready", "box_cardboard_a01", REVISION, view="full")
    assert canonical_bytes(summary) <= 8192
    assert canonical_bytes(full) <= 32768
    assert full.get("previewTruncated") is True


def test_describe_with_many_installed_versions_stays_bounded(tmp_path):
    versions = []
    for revision in range(1, 21):
        version = make_version(
            tmp_path, f"box-{revision:02d}", revision,
            provenance={
                "sourceRevision": REVISION,
                "sourcePackage": "box_cardboard_a01",
                "license": {"name": "CC-BY-4.0"},
            },
        )
        (version.path / "LICENSE.txt").write_text("CC-BY-4.0", encoding="utf-8")
        versions.append(version)
    value = catalog()
    summary = value.describe(
        "simready", "box_cardboard_a01", REVISION, library=FakeLibrary(versions)
    )
    full = value.describe(
        "simready", "box_cardboard_a01", REVISION, library=FakeLibrary(versions), view="full"
    )
    assert canonical_bytes(summary) <= 8192
    assert canonical_bytes(full) <= 32768
    assert summary["installed"]["truncated"] is True
    assert summary["previewTruncated"] is True
    assert len(summary["installed"]["versions"]) < 20


def test_describe_clips_a_huge_installed_license_name(tmp_path):
    version = make_version(
        tmp_path, "huge-license", 1,
        provenance={
            "sourceRevision": REVISION,
            "sourcePackage": "box_cardboard_a01",
            "license": {"name": "L" * 50_000},
        },
    )
    (version.path / "LICENSE.txt").write_text("fixture", encoding="utf-8")
    value = catalog()
    full = value.describe(
        "simready", "box_cardboard_a01", REVISION,
        library=FakeLibrary([version]), view="full",
    )
    assert canonical_bytes(full) <= 32768
    assert len(full["installed"]["versions"][0]["license"]["name"]) <= 200


def test_zero_result_with_long_coverage_terminates_within_budget(tmp_path):
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    data["coverage"] = "C" * 10_000
    path = tmp_path / "long-coverage.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    value = catalog(path)
    result = value.search(EmptyLibrary(), q="9" * 40_000 + "m", scope="online")
    assert canonical_bytes(result) <= 32768
    assert result["zeroResult"] is not None


def test_cart_describe_exposes_unknowns_and_gap():
    value = catalog()
    full = value.describe("simready", "equipment_utility_cart_e01", REVISION, view="full")
    assert full["unknown"] == ["dimensionsM", "dressing", "staticCollision"]
    assert full["adaptation"]["dimensionsM"] is None
    assert full["adaptation"]["status"] == "gap"
    assert "UDIM" in full["adaptation"]["gaps"][0]
    assert full["acquisition"]["eligible"] is False
    assert "UDIM" in full["acquisition"]["reasons"][0]


def test_estimate_reports_budgets_cache_and_special_cases():
    value = catalog()
    box = value.estimate("simready", "box_cardboard_a01", REVISION)
    assert box["schema"] == ESTIMATE_SCHEMA
    assert box["source"]["bytes"] == 21530693
    assert box["source"]["files"] == 36
    assert box["source"]["licenseFiles"] == [".metadata/LICENSE.txt"]
    assert box["source"]["bomItems"] == 30
    assert box["transfer"] == {"upperBoundBytes": 21530693}
    assert box["temporary"] == {"upperBoundBytes": 21530693}
    assert box["cache"] == {
        "reusedBytes": None, "known": False,
        "reason": "Raw cache is not implemented before SF2.",
    }
    assert box["adaptedPackage"] == {
        "bytes": 19085294, "upperBoundBytes": 19085294, "known": True,
    }
    assert box["requiredDiskBytes"] == 21530693 + 19085294 + 10737418240
    assert box["limits"] == {
        "sourcePackageBytes": 536870912,
        "adaptedPackageBytes": 536870912,
        "batchBytes": 2147483648,
        "rawCacheBytes": 10737418240,
        "freeDiskMinBytes": 10737418240,
    }
    assert box["withinLimits"] is True
    assert box["license"] == {
        "admitted": True,
        "reasons": ["attribution", "retain license and notices", "mark modifications"],
    }
    assert box["usage"] == {"dressing": True, "staticCollision": True}
    assert box["acquisition"] == {"eligible": True, "reasons": []}
    assert box["unknown"] == []
    assert box["networkRequired"] is True
    assert box["downloadsMainFiles"] is False
    assert box["estimated"] is True

    desk = value.estimate("simready", "desk_a01", REVISION)
    assert desk["adaptedPackage"] == {
        "bytes": 297991931, "upperBoundBytes": 297991931, "known": True,
    }
    assert desk["withinLimits"] is True
    assert desk["acquisition"]["eligible"] is False
    assert any("134217728" in reason for reason in desk["acquisition"]["reasons"])
    assert desk["unknown"] == ["dressing", "staticCollision"]

    cart = value.estimate("simready", "equipment_utility_cart_e01", REVISION)
    assert cart["adaptedPackage"] == {
        "bytes": None, "upperBoundBytes": 536870912, "known": False,
    }
    assert cart["requiredDiskBytes"] == 171038176 + 536870912 + 10737418240
    assert cart["withinLimits"] is True
    assert cart["acquisition"]["eligible"] is False
    assert cart["usage"] == {"dressing": None, "staticCollision": None}

    container = value.estimate("simready", "container_recycle_square_blue_a02", REVISION)
    assert container["acquisition"]["eligible"] is True
    assert container["usage"] == {"dressing": True, "staticCollision": False}


def test_soft_preferences_are_recorded_but_never_become_hard_filters():
    value = catalog()
    result = value.search(EmptyLibrary(), q="small box", scope="online", min_dimension_m=2)
    interpreted = result["interpretedQuery"]
    assert interpreted["preferences"] == [
        {"term": "small", "language": "en", "applied": False},
    ]
    assert interpreted["keywords"] == ["box"]
    assert "Soft preference 'small' does not relax minDimensionM=2." in interpreted["notes"]
    assert result["results"] == []
    assert result["zeroResult"]["reason"] == "hard_filters"
    chinese = value.search(EmptyLibrary(), q="小 纸箱", scope="online")
    assert chinese["interpretedQuery"]["preferences"] == [
        {"term": "小", "language": "zh", "applied": False},
    ]
    assert package(chinese) == ["box_cardboard_a01"]
    assert any("not converted" in note for note in chinese["interpretedQuery"]["notes"])


def test_number_unit_query_terms_are_not_converted_to_filters():
    result = catalog().search(EmptyLibrary(), q="box 2m", scope="online")
    assert result["interpretedQuery"]["keywords"] == ["box"]
    assert any("was not converted to a numeric filter" in note
               for note in result["interpretedQuery"]["notes"])
    assert package(result) == ["box_cardboard_a01"]
    numeric_only = catalog().search(EmptyLibrary(), q="2m", scope="online")
    assert numeric_only["results"] == []
    assert numeric_only["zeroResult"]["reason"] == "no_query_match"


def test_empty_catalog_is_reported_explicitly(tmp_path):
    value = catalog(write_catalog(tmp_path, []))
    online = value.search(EmptyLibrary(), q="box", scope="online")
    assert online["zeroResult"]["reason"] == "empty_catalog"
    combined = value.search(EmptyLibrary(), q="box", scope="all")
    assert combined["zeroResult"]["reason"] == "empty_catalog"
    assert online["snapshot"]["entryCount"] == 0


def test_catalog_unavailable_leaves_local_search_and_exact_local_ids_working(tmp_path):
    missing = AssetCatalog(tmp_path / "missing.json")
    missing.startup()
    assert missing.available is False
    assert "unavailable" in missing.error.lower()
    with pytest.raises(LibraryError) as unavailable:
        missing.search(EmptyLibrary(), q="纸箱", scope="online")
    assert unavailable.value.status == 503
    assert unavailable.value.detail["code"] == "catalog_unavailable"
    with pytest.raises(LibraryError):
        missing.search(EmptyLibrary(), q="纸箱", scope="all")
    version = make_version(tmp_path, "lab-crate", 1)
    library = FakeLibrary([version])
    local = missing.search(library, q="lab crate", scope="local")
    assert ids(local) == ["environment/lab-crate@1"]
    assert local["snapshot"]["id"] is None
    assert local["snapshot"]["offlineReadable"] is False
    assert any("catalog is unavailable" in note.lower()
               for note in local["interpretedQuery"]["notes"])
    exact = missing.search(library, q="environment/lab-crate", scope="local")
    assert ids(exact) == ["environment/lab-crate@1"]


def test_invalid_catalog_files_are_reported_without_crashing(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    value = AssetCatalog(path)
    value.startup()
    assert value.available is False
    assert value.error
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    data["schema"] = "other"
    path.write_text(json.dumps(data), encoding="utf-8")
    value = AssetCatalog(path)
    value.startup()
    assert value.available is False
    with pytest.raises(ValueError):
        load_catalog(path)
