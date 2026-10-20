"""Dependency authority uses durable, bound receipts and explicit byte identities."""

from types import SimpleNamespace

import pytest

from packages.library.reports import ReportStore, pinned_assets


def version(tmp_path, entries):
    return SimpleNamespace(
        id="fixture@1",
        content_hash="b" * 64,
        reports=ReportStore(tmp_path),
        validation={
            "schema": "lw.validation/1",
            "subject": "scene:fixture@1",
            "launchable": True,
            "checks": [],
            "referencedAssets": entries,
        },
    )


def test_a_durable_report_without_a_latest_pointer_still_establishes_known_bytes(tmp_path):
    selected = version(tmp_path, [])
    pin = {"id": "device/fixture@1", "contentHash": "a" * 64}
    selected.reports.save(selected.validation | {"referencedAssets": [pin]}, selected.content_hash)
    (selected.reports.directory("scene:fixture@1", selected.content_hash) / "latest.json").unlink()
    assert (
        selected.reports.latest("scene:fixture@1", selected.content_hash, selected.validation)
        == selected.validation
    )
    assert pinned_assets(selected, "scene") == [pin]


@pytest.mark.parametrize(
    "entry", [{"id": "device/fixture@1"}, {"id": "device/fixture@1", "contentHash": None}]
)
def test_legacy_hashless_entries_establish_no_historical_digest(tmp_path, entry):
    assert pinned_assets(version(tmp_path, [entry]), "scene") == []


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "device/fixture@1", "contentHash": "invalid"},
        {"id": "device/fixture@01", "contentHash": "a" * 64},
        {"contentHash": "a" * 64},
        "device/fixture@1",
    ],
)
def test_malformed_recorded_id_or_digest_fails_closed_instead_of_establishing_a_new_pin(tmp_path, entry):
    with pytest.raises(ValueError):
        pinned_assets(version(tmp_path, [entry]), "scene")
