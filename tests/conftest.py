"""Keep automatic recording recovery/retention isolated between every test app."""

import pytest

from tests.ci_sharding import (
    pytest_collection_modifyitems as pytest_collection_modifyitems,
)
from tests.ci_sharding import (
    pytest_sessionfinish as pytest_sessionfinish,
)
from tests.ci_sharding import (
    pytest_terminal_summary as pytest_terminal_summary,
)


@pytest.fixture(autouse=True)
def isolated_recordings(tmp_path, monkeypatch):
    monkeypatch.setenv("SPATIAL_RECORDINGS_ROOT", str(tmp_path / "recordings"))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "user-library"))
