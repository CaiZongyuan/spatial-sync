"""No-Newton report composition never converts unmeasured warnings into evidence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from packages.library import reports
from packages.library.assets import AssetLibrary, AssetVersion
from packages.library.scenes import SceneLibrary
from packages.library.validation import StructuralValidator

SUBJECT = "scene:physics-fixture@1"


def structure():
    return {"schema": "lw.validation/1", "subject": SUBJECT, "launchable": True,
            "toolVersions": {"usd-core": "26.8"},
            "checks": [{"id": "S8", "tier": "structural", "status": "pass", "message": "Valid posture"},
                       {"id": "P1-P6", "tier": "physics", "status": "warn", "message": "Not run"}],
            "referencedAssets": [{"id": "robot/fixture@1", "contentHash": "c" * 64}],
            "effectiveBindings": {"newton": [{"key": "/World/Arm"}]}}


def completed(*, status="fail", identifier="P6", ran_at="2026-10-20T01:00:00+00:00"):
    return {"physics": {"status": "completed", "device": "cpu", "ranAt": ran_at,
                        "toolVersions": {"newton": "1.7.0", "warp-lang": "1.18.0"}},
            "checks": [{"id": identifier, "tier": "physics", "status": status,
                        "entityKey": "/World/Arm", "location": "station", "message": "Measured"}]}


def skipped():
    return {"physics": {"status": "skipped", "reason": "Newton is not installed",
                        "missingTools": ["newton"]},
            "checks": [{"id": "P6", "tier": "physics", "status": "warn", "message": "Not run"}]}


def version(tmp_path):
    return SimpleNamespace(id="physics-fixture@1", content_hash="a" * 64,
                           validation=structure(), reports=reports.ReportStore(tmp_path))


@pytest.mark.parametrize("identifier", ["P1", "P6"])
def test_skip_retains_real_failure_with_original_execution_provenance(tmp_path, identifier):
    selected = version(tmp_path)
    measured = reports.merge_physics(structure(), completed(identifier=identifier))
    saved = selected.reports.save(measured, selected.content_hash)
    retained = reports.latest_physics_evidence(selected, "scene")
    outcome = reports.merge_physics(structure(), skipped(), retained)
    assert not outcome["launchable"]
    assert any(row["id"] == identifier and row["status"] == "fail" for row in outcome["checks"])
    assert outcome["physics"]["retainedFrom"]["reportId"] == saved["reportId"]
    assert outcome["physics"]["retainedFrom"]["ranAt"] == measured["physics"]["ranAt"]
    assert outcome["referencedAssets"] == structure()["referencedAssets"]
    assert outcome["effectiveBindings"] == structure()["effectiveBindings"]


def test_real_new_completion_can_replace_failure_but_skip_and_missing_latest_cannot(tmp_path):
    selected = version(tmp_path)
    failed = selected.reports.save(reports.merge_physics(structure(), completed()), selected.content_hash)
    selected.reports.save(reports.merge_physics(structure(), skipped(), failed), selected.content_hash)
    (selected.reports.directory(SUBJECT, selected.content_hash) / "latest.json").unlink()
    assert reports.latest_physics_evidence(selected, "scene")["reportId"] == failed["reportId"]
    passed = selected.reports.save(
        reports.merge_physics(structure(), completed(status="pass")), selected.content_hash
    )
    retained = reports.latest_physics_evidence(selected, "scene")
    assert retained["reportId"] == passed["reportId"]
    assert reports.merge_physics(structure(), skipped(), retained)["launchable"]


def test_legacy_placeholder_never_establishes_completed_physics(tmp_path):
    selected = version(tmp_path)
    assert reports.latest_physics_evidence(selected, "scene") is None
    result = reports.merge_physics(structure(), skipped())
    assert result["physics"]["status"] == "skipped" and "retainedFrom" not in result["physics"]
    with pytest.raises(ValueError, match="placeholder"):
        reports.parse_report(structure() | {"physics": completed()["physics"]})


def test_legacy_individual_physics_failure_is_preserved_conservatively(tmp_path):
    selected = version(tmp_path)
    selected.validation["checks"][-1] = completed()["checks"][0]
    retained = reports.latest_physics_evidence(selected, "scene")
    assert not reports.merge_physics(structure(), skipped(), retained)["launchable"]


def test_foreign_raw_hash_evidence_does_not_affect_this_version(tmp_path):
    selected = version(tmp_path)
    selected.reports.save(reports.merge_physics(structure(), completed()), "b" * 64)
    assert reports.latest_physics_evidence(selected, "scene") is None


def test_missing_tool_observation_changes_to_stale_when_installed_without_sdk_import(monkeypatch):
    result = reports.merge_physics(structure(), skipped())

    def missing(name):
        if name == "newton":
            raise reports.PackageNotFoundError(name)
        return "26.8"

    monkeypatch.setattr(reports, "installed_version", missing)
    assert reports.validation_status(result) == "available"
    monkeypatch.setattr(reports, "installed_version", lambda name: "1.7.0" if name == "newton" else "26.8")
    assert reports.validation_status(result) == "stale"
    assert result["launchable"]  # Availability/staleness does not rewrite recorded checks.


def test_retained_physics_versions_are_checked_independently_and_inputs_stay_unchanged(monkeypatch):
    source = structure()
    measured = reports.merge_physics(source, completed())
    old = deepcopy((source, measured))
    result = reports.merge_physics(source, skipped(), measured)
    assert (source, measured) == old
    versions = {"newton": "1.8.0", "warp-lang": "1.18.0", "usd-core": "26.8"}
    monkeypatch.setattr(reports, "installed_version", versions.__getitem__)
    assert reports.validation_status(result) == "stale"
    assert not result["launchable"]


@pytest.mark.parametrize("layer", [
    {"status": "skipped"}, {"status": "completed"},
    {"status": "skipped", "reason": "Missing", "missingTools": ["newton", "newton"]},
])
def test_layer_requires_explicit_truthful_availability_or_execution_identity(layer):
    with pytest.raises(ValueError):
        reports.parse_report(structure() | {"physics": layer})


@pytest.mark.parametrize("case", ["completed", "failed", "partial", "skipped"])
def test_asset_physics_gap_and_filter_follow_complete_successful_measurement(tmp_path, case):
    path = tmp_path / "asset"
    path.mkdir()
    for name in ("LICENSE", "provenance.json"):
        (path / name).write_text("fixture", encoding="utf-8")
    source = structure() | {"subject": "asset:labware/physics-fixture@1"}
    source["checks"] = [{"id": f"S{i}", "tier": "structural", "status": "pass", "message": "Valid"}
                        for i in range(1, 9)]
    source["completeness"] = [
        {"id": name, "status": "present", "message": "Inspected"}
        for name in ("collision", "massInertia", "visual", "license", "jointSites", "types")
    ]
    outcome = completed(status="pass")
    outcome["checks"] = [
        {"id": f"P{i}", "tier": "physics", "status": "pass", "message": "Measured"}
        for i in range(1, 5)
    ]
    if case == "failed":
        outcome["checks"][-1]["status"] = "fail"
    if case == "partial":
        outcome["checks"] = outcome["checks"][:1]
    measured = reports.merge_physics(source, outcome)
    report = reports.merge_physics(source, skipped(), measured) if case == "skipped" else measured
    metadata = {"kind": "labware", "title": "Fixture", "visual": "visual.glb", "dimensionsM": [1., 1., 1.],
                "physics": {"massKg": 1., "collision": "authored"}}
    selected = AssetVersion("labware/physics-fixture@1", 1, "user", path, metadata, "a" * 64, report, ())
    library = AssetLibrary(tmp_path / "state", builtin_root=tmp_path / "builtin", user_root=tmp_path / "user")
    library.versions = {selected.id: selected}
    assert ("physics_validation" in selected.summary()["gaps"]) is (case != "completed")
    assert bool(library.list(only_gaps=True)) is (case != "completed")


def commit_fixture(tmp_path):
    root = tmp_path / "user"
    path = root / "scenes/physics-fixture/1"
    path.mkdir(parents=True)
    (path / "scene.yaml").write_text(
        "name: physics-fixture\ntitle: Fixture\nsources: [newton]\nstage: python:falling-boxes\n",
        encoding="utf-8",
    )
    (path / "devices.yaml").write_text("scene: physics-fixture\nentities: []\n", encoding="utf-8")
    (path / "locations.yaml").write_text("locations: {}\n", encoding="utf-8")
    assets = AssetLibrary(tmp_path / "state", builtin_root=tmp_path / "builtin", user_root=root)
    scene = SceneLibrary(builtin_root=tmp_path / "builtin", user_root=root, state_root=tmp_path / "state")
    selected = scene.resolve("physics-fixture@1")
    source = structure() | {"referencedAssets": []}
    return assets, selected, source


@pytest.mark.parametrize("identifier", ["P1", "P6"])
def test_final_commit_reads_later_failure_under_shared_library_lock(tmp_path, monkeypatch, identifier):
    import packages.library.validation as validation

    assets, selected, source = commit_fixture(tmp_path)
    pending_skip = reports.merge_physics(source, skipped())
    later = selected.reports.save(reports.merge_physics(source, completed(identifier=identifier)),
                                   selected.content_hash)
    read = validation.latest_physics_evidence

    def locked_read(version, kind):
        assert assets._lock.locked()
        return read(version, kind)

    monkeypatch.setattr(validation, "latest_physics_evidence", locked_read)
    result = StructuralValidator(tmp_path / "state").commit(selected, "scene", assets, pending_skip)
    assert not result["launchable"]
    assert any(row["id"] == identifier and row["status"] == "fail" for row in result["checks"])
    assert result["physics"]["retainedFrom"]["reportId"] == later["reportId"]
    assert selected.report() == result


def test_final_skip_retains_genuine_new_success_instead_of_unioning_historical_failures(tmp_path):
    assets, selected, source = commit_fixture(tmp_path)
    selected.reports.save(reports.merge_physics(source, completed()), selected.content_hash)
    pending_skip = reports.merge_physics(source, skipped())
    later = selected.reports.save(
        reports.merge_physics(source, completed(status="pass")), selected.content_hash
    )
    result = StructuralValidator(tmp_path / "state").commit(selected, "scene", assets, pending_skip)
    assert result["launchable"]
    assert result["physics"]["retainedFrom"]["reportId"] == later["reportId"]
    assert not any(row["status"] == "fail" for row in result["checks"])
