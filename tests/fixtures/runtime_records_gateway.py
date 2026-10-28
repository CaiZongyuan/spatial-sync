"""Actual runtime record intake/store, with every spatial Source forbidden."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
for variable, directory in (
    ("LW_LIBRARY", "library"),
    ("SPATIAL_STATE_ROOT", "state"),
    ("SPATIAL_RECORDINGS_ROOT", "recordings"),
):
    os.environ.setdefault(variable, str(ROOT / ".scratch/runtime-records-browser" / directory))

from apps.api.app.main import create_app  # noqa: E402
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system  # noqa: E402
from apps.api.app.settings import Settings  # noqa: E402


class Sampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Runtime import browser fixture; no host probes"))


def no_source(*args):
    raise AssertionError("Runtime import must not create a spatial Source")


origins = tuple(filter(None, os.getenv("SPATIAL_ALLOWED_ORIGINS", "").split(",")))
app = create_app(
    Settings(node_trainer=(), allowed_origins=origins),
    source_factory=no_source,
    platform_sampler=Sampler(),
)
