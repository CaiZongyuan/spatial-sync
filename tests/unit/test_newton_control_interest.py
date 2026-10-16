"""Local adapter barriers cover packets queued on the independent frame pipe."""

import asyncio
from types import SimpleNamespace

import pytest

from apps.api.app.sources.newton_source import NewtonSource
from packages.protocol.schema import SessionOptions, TransformFrame
from packages.protocol.scripts.export_schema import manifest_sample


def frame(seq, ids=(7,)):
    return TransformFrame(
        sessionId="session-fixture", sceneRevision="rev-fixture", epoch=0,
        seq=seq, simTime=seq / 10, sampleTimeMs=seq * 100,
        poses=[(value, seq, 0, 1, 0, 0, 0, 1) for value in ids],
    )


async def test_transition_slot_is_discarded_and_late_readonly_snapshot_reply_does_not_poison_channel():
    source = NewtonSource("session-fixture")
    source._loop = asyncio.get_running_loop()
    source._ready = source._loop.create_future()
    source.options = SessionOptions()
    source._on_control({
        "kind": "ready", "manifest": manifest_sample().model_dump(), "snapshot": frame(1).model_dump(),
    })

    def interest_response(packet):
        assert source._samples.transition is True
        source._incoming = source._codec.encode(frame(4, ()))  # new targets arrive before ack S=3
        source._on_control({
            "kind": "interest_result", "token": packet["token"], "streamIds": [],
            "frame": frame(3).model_copy(update={"type": "stream.snapshot"}).model_dump(),
        })

    source._writer = SimpleNamespace(offer=interest_response)
    assert (await source.set_interest(frozenset())).seq == 3
    source._drain_frame()
    assert source.latest_frame().seq == 3  # pre-ack seq4 was discarded, not relabeled as a fresh receipt
    source._incoming = source._codec.encode(frame(5, ()))
    source._drain_frame()
    assert source.latest_frame().seq == 5 and source.latest_frame().poses == []

    source._writer = SimpleNamespace(offer=lambda packet: None)
    with pytest.raises(TimeoutError):
        await source._request("snapshot", timeout=0.001)
    assert source._failed is False
    source._on_control({
        "kind": "snapshot_result", "token": "expired", "streamIds": [],
        "frame": frame(6).model_copy(update={"type": "stream.snapshot"}).model_dump(),
    })
    assert source._samples.complete.seq == 6 and source._samples.expected_ids == frozenset()

    def snapshot_response(packet):
        source._on_control({
            "kind": "snapshot_result", "token": packet["token"], "streamIds": [],
            "frame": frame(7).model_copy(update={"type": "stream.snapshot"}).model_dump(),
        })

    source._writer = SimpleNamespace(offer=snapshot_response)
    assert (await source.snapshot()).seq == 7 and source._failed is False
    source._incoming = source._codec.encode(frame(8, ()))
    source._drain_frame()
    assert source.latest_frame().seq == 8
    source._incoming = source._codec.encode(frame(9))  # malformed membership after installed empty interest
    source._drain_frame()
    assert source._failed is True
