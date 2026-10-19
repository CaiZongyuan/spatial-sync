"""Keep automatic recording recovery/retention isolated between every test app."""

import pytest


@pytest.fixture(autouse=True)
def isolated_recordings(tmp_path, monkeypatch):
    monkeypatch.setenv("SPATIAL_RECORDINGS_ROOT", str(tmp_path / "recordings"))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "user-library"))
