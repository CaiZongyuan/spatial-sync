"""The published report schema stays synchronized and reads existing immutable reports."""

import json
from pathlib import Path

import pytest

from packages.library.reports import launchable, parse_report
from tools.export_validation_schema import schema

ROOT = Path(__file__).resolve().parents[2]


def test_validation_schema_artifact_is_current():
    assert (
        json.loads((ROOT / "packages/library/schemas/validation.schema.json").read_text(encoding="utf-8"))
        == schema()
    )


@pytest.mark.parametrize("path", sorted((ROOT / "library").glob("**/validation.json")))
def test_existing_published_validation_documents_are_still_readable_without_rewriting(path):
    original = json.loads(path.read_text(encoding="utf-8"))
    assert parse_report(original) == original


@pytest.mark.parametrize(
    "invalid",
    [
        {"schema": "lw.validation/2"},
        {"subject": "scene:../outside@1"},
        {"launchable": "true"},
        {"ranAt": "2026-10-19T12:00:00"},
        {"checks": [{"id": "S1", "tier": "structural", "status": "unknown", "message": "fixture"}]},
    ],
)
def test_invalid_report_contract_is_rejected(invalid):
    report = {
        "schema": "lw.validation/1",
        "subject": "scene:contract-fixture@1",
        "checks": [],
        "launchable": True,
    }
    with pytest.raises(ValueError):
        parse_report(report | invalid)


def test_failed_check_cannot_be_overridden_by_launch_flag_and_old_partial_reports_remain_valid():
    partial = {
        "schema": "lw.validation/1",
        "subject": "scene:contract-fixture@1",
        "checks": [],
        "launchable": True,
    }
    assert launchable(parse_report(partial))
    failed = partial | {
        "checks": [{"id": "S8", "tier": "structural", "status": "fail", "message": "Bad posture"}]
    }
    assert not launchable(parse_report(failed))
