"""Owned Synthetic-only gateway for external handoff process-restart checks."""

from apps.api.app.main import create_app
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource


class Sampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("External evaluation fixture; no host probes"))


def synthetic(kind, session_id):
    if kind != "synthetic":
        raise AssertionError("This public controller fixture only runs Synthetic")
    return SyntheticSource(session_id)


app = create_app(
    Settings(node_trainer=(), remote=False), source_factory=synthetic, platform_sampler=Sampler()
)
