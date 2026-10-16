"""Complete frame authority survives independent frame/control Pipe delivery order."""

import pytest

from packages.protocol.schema import SceneManifest, TransformFrame
from packages.transport.source_state import RunnerHeartbeat, SourceFrames


def samples():
    manifest = SceneManifest(
        sessionId="sample-test",
        sceneRevision="rev-test",
        revisionNumericId=1,
        sceneName="samples",
        source={"kind": "synthetic", "physicsDt": 1 / 240, "basePublishHz": 30},
        nodes=[
            {"kind": "body", "entityKey": "/one", "name": "one", "streamId": 3},
            {"kind": "body", "entityKey": "/two", "name": "two", "streamId": 17},
        ],
    )
    return SourceFrames(manifest)


def frame(seq, ids=(3, 17), **updates):
    return TransformFrame(
        sessionId="sample-test", sceneRevision="rev-test", epoch=0,
        seq=seq, simTime=seq / 10, sampleTimeMs=seq * 100,
        poses=[(value, seq, value, 0, 0, 0, 0, 1) for value in ids],
    ).model_copy(update=updates)


def test_command_frame_blocks_older_periodic_packets_and_cache_never_moves_backwards():
    state = samples()
    state.control(frame(1))
    state.control(frame(3, epoch=1))
    # The frame Pipe may deliver old data after the reliable reset result.
    assert state.periodic(frame(2)) is False
    assert state.latest.seq == 3
    assert state.latest.epoch == 1
    assert state.periodic(frame(5, epoch=1)) is True
    state.control(frame(4, epoch=1))  # a snapshot reply can trail newer periodic data
    assert state.latest.seq == 5
    assert state.periodic(frame(3)) is False
    assert all(row[1] == 5 for row in state.latest.poses)


@pytest.mark.parametrize("ids", [(3,), (3, 999), ()])
@pytest.mark.parametrize("channel", ["periodic", "control"])
def test_every_capture_requires_the_exact_manifest_body_set(ids, channel):
    state = samples()
    state.control(frame(1))
    with pytest.raises(ValueError, match="exact expected body set"):
        getattr(state, channel)(frame(2, ids))
    assert state.latest.seq == 1


@pytest.mark.parametrize("updates", [{"sessionId": "wrong"}, {"sceneRevision": "wrong"}])
def test_capture_cannot_change_its_manifest_identity(updates):
    with pytest.raises(ValueError, match="identity"):
        samples().control(frame(1, **updates))


def test_control_responses_cannot_refresh_an_unchanged_runner_heartbeat():
    clock = [50.0]
    heartbeat = RunnerHeartbeat(lambda: clock[0])
    assert heartbeat.stats(True)["runnerHeartbeatMs"] is None
    heartbeat.observe({"runnerHeartbeatSeq": 2, "runnerHeartbeatMs": 200})
    clock[0] += 2
    heartbeat.observe({"runnerHeartbeatSeq": 2, "runnerHeartbeatMs": 10})
    assert heartbeat.stats(True)["runnerHeartbeatMs"] == pytest.approx(2200)
    clock[0] += 1
    assert heartbeat.stats(True)["runnerHeartbeatAlive"] is False
    heartbeat.observe({"runnerHeartbeatSeq": 3, "runnerHeartbeatMs": 20})
    assert heartbeat.stats(True)["runnerHeartbeatMs"] == pytest.approx(20)
    assert heartbeat.stats(False)["runnerHeartbeatAlive"] is False
