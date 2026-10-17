"""HTTP/WS tests exercise lifecycle without probing the developer's host GPUs."""

import pytest

from apps.api.app import main
from apps.api.app.platform_monitor import PlatformMonitor, platform_snapshot, unavailable_system


class FakeHostSampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Host probes are disabled in gateway tests"))


@pytest.fixture(autouse=True)
def fake_platform_sampler(monkeypatch):
    def monitor(manager, sampler=None):
        return PlatformMonitor(manager, sampler=FakeHostSampler() if sampler is None else sampler)

    monkeypatch.setattr(main, "PlatformMonitor", monitor)
