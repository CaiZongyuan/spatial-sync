"""A coarse host clock must not accumulate a new delay after every publication."""

import asyncio
import math

import pytest

import apps.api.app.sources.synthetic_source as synthetic_module
from packages.protocol.schema import SessionOptions


async def test_publication_preserves_rate_under_coarse_wakeups(monkeypatch):
    source = synthetic_module.SyntheticSource("cadence")
    now = 0.0
    publications = []
    finished = asyncio.Event()
    last_seq = 0

    async def coarse_sleep(delay):
        nonlocal now, last_seq
        frame = source.latest_frame()
        if frame and frame.seq != last_seq:
            last_seq = frame.seq
            if frame.type == "transform.frame":
                publications.append(now)
        if now >= 2:
            finished.set()
            await asyncio.Event().wait()  # interrupted by the public close lifecycle below
            return
        now += max(1, math.ceil(delay / (1 / 64))) / 64

    monkeypatch.setattr(synthetic_module.asyncio, "sleep", coarse_sleep)
    monkeypatch.setattr(synthetic_module.time, "monotonic", lambda: now)
    try:
        await source.start("lab-demo", SessionOptions())
        await finished.wait()
        # Sixty real unique publication slots in two seconds, with no catch-up burst.
        assert 59 <= len(publications) <= 60
        assert min(b - a for a, b in zip(publications, publications[1:])) >= 1 / 64
        assert source.sim_time == pytest.approx(2, abs=source.options.physicsDt)
    finally:
        await source.close()
