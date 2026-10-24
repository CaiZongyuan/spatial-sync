"""Private recording authority survives both layer shapes and Session startup/recovery."""

import time
from types import SimpleNamespace

from packages.agent_evaluation.scope import CaseGrant, observation_scope
from packages.info.evaluation_privacy import private_recording, visible_session, visible_session_id


def test_cases_episodes_and_starting_session_share_the_same_durable_authority():
    records = {
        "test-agent": {"split": "test", "cases": [{"recordingId": "agent-recording"}]},
        "test-embodied": {"split": "test", "episodes": [{"recordingId": "embodied-recording"}]},
        "train": {"split": "train", "episodes": [{"recordingId": "public-recording"}]},
    }
    manager = SimpleNamespace(
        evaluations=SimpleNamespace(store=SimpleNamespace(records=records)),
        sessions={},
        _starting_session=SimpleNamespace(session_id="starting", evaluation_id="test-embodied"),
    )
    assert all(
        private_recording(manager, identity)
        for identity in ("agent-recording", "embodied-recording", "starting")
    )
    assert not private_recording(manager, "public-recording")
    manager._starting_session = None
    assert private_recording(manager, "embodied-recording")
    assert not visible_session_id(manager, "embodied-recording")
    assert not visible_session_id(manager, "agent-recording")
    assert visible_session_id(manager, "public-recording")


def test_held_out_session_is_visible_only_inside_its_actual_case_grant():
    session = SimpleNamespace(evaluation_id="private", state="RUNNING")
    other = SimpleNamespace(evaluation_id="private", state="RUNNING")
    manager = SimpleNamespace(
        evaluations=SimpleNamespace(store=SimpleNamespace(records={"private": {"split": "test"}}))
    )
    assert not visible_session(manager, session)
    grant = CaseGrant(
        "private", {"caseRunId": "case-one", "status": "running"}, session, time.monotonic() + 30, audit=None
    )
    with observation_scope(grant):
        assert visible_session(manager, session)
        assert not visible_session(manager, other)
