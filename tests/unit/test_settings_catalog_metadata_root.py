"""The metadata library is configured separately from the experiment state root."""

from pathlib import Path

import pytest

from apps.api.app.settings import Settings


def test_metadata_root_reads_an_absolute_directory(monkeypatch, tmp_path):
    target = tmp_path / "library"
    monkeypatch.setenv("SPATIAL_CATALOG_METADATA_ROOT", str(target))
    assert Settings().catalog_metadata_root == target


def test_metadata_root_is_unset_by_default():
    assert Settings().catalog_metadata_root is None


def test_metadata_root_expands_a_home_directory(monkeypatch):
    monkeypatch.setenv("SPATIAL_CATALOG_METADATA_ROOT", "~/.spatial-sync/state/catalog-metadata")
    assert Settings().catalog_metadata_root == Path.home() / ".spatial-sync/state/catalog-metadata"


@pytest.mark.parametrize("value", ["catalog-metadata", "./catalog-metadata", "../library"])
def test_a_relative_metadata_root_is_rejected(monkeypatch, value):
    monkeypatch.setenv("SPATIAL_CATALOG_METADATA_ROOT", value)
    with pytest.raises(ValueError, match="absolute"):
        Settings()
