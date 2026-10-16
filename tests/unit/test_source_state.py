"""Partial captures cannot impersonate complete recovery samples or refresh a stopped Runner."""

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


def test_interest_barrier_discards_old_membership_and_early_new_packets_without_stale_row_merges():
    state = samples()
    state.control(frame(1))
    state.transition = True  # before the interest RPC can let new targets publish
    assert state.periodic(frame(4, (17,))) is False
    state.install_interest(frozenset([17]), frame(3))
    # Old target packets are discarded before checking the new membership.
    assert state.periodic(frame(2)) is False
    assert state.latest.seq == 3
    assert state.periodic(frame(5, (17,))) is True
    assert [row[0] for row in state.latest.poses] == [17]
    assert state.complete.seq == 3 and all(row[1] == 3 for row in state.complete.poses)
    with pytest.raises(ValueError, match="exact expected body set"):
        state.periodic(frame(6))
    with pytest.raises(ValueError, match="exact expected body set"):
        state.control(frame(6, (17,)))
    state.control(frame(7))  # an HTTP full capture does not expand periodic targets
    assert state.periodic(frame(8, (17,))) is True
    assert state.complete.seq == 7 and all(row[1] == 7 for row in state.complete.poses)


@pytest.mark.parametrize("ids", [[3, 3], [True], [1.5], [999]])
def test_interest_validation_cannot_mutate_accepted_membership(ids):
    state = samples()
    with pytest.raises(ValueError):
        state.interest(ids)
    assert state.expected_ids == frozenset([3, 17])


def test_runner_relative_age_survives_duplicate_proxy_heartbeats_and_failure():
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
