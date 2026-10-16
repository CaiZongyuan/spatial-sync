"""A coarse host clock must not accumulate a new delay after every publication."""

import math

import pytest

import apps.api.app.sources.synthetic_source as synthetic_module


async def test_publication_preserves_rate_under_coarse_wakeups(monkeypatch):
    source = synthetic_module.SyntheticSource("cadence")
    source._state = "RUNNING"
    source._last_clock = 0
    now = 0.0
    publications = []

    class Finished(Exception):
        pass

    async def coarse_sleep(delay):
        nonlocal now
        now += max(1, math.ceil(delay / (1 / 64))) / 64
        if now > 2:
            raise Finished

    monkeypatch.setattr(synthetic_module.asyncio, "sleep", coarse_sleep)
    monkeypatch.setattr(synthetic_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(source, "_publish", lambda: publications.append(now))
    with pytest.raises(Finished):
        await source._run()
    # Sixty unique publication slots in two seconds, with no catch-up burst.
    assert 59 <= len(publications) <= 60
    assert min(b - a for a, b in zip(publications, publications[1:])) >= 1 / 64
    assert source.sim_time == pytest.approx(2, abs=source.options.physicsDt)
