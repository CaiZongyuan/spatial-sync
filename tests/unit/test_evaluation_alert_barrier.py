"""The browser fixture retains a real graded episode without owning its task."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from tests.fixtures.evaluation_barrier import EvaluationAlertBarriers


@pytest.fixture
def fixture():
    suite = SimpleNamespace(
        id="suite/evaluation-alerts@1", content_hash="suite-hash",
        metadata={"scene": "evaluation-alerts@1", "sceneContentHash": "scene-hash",
                  "episodes": [{"id": "train-one", "split": "train"}]},
    )
    outcome = {"caseId": "train-one", "success": True, "invocationId": "actual-invocation"}
    service = SimpleNamespace(
        _episode=AsyncMock(return_value=outcome),
        suites=SimpleNamespace(resolve=lambda _: suite),
        store=SimpleNamespace(records={
            "evaluation-owned": {
                "suite": suite.id, "suiteContentHash": suite.content_hash, "status": "queued",
            },
        }),
    )
    session = SimpleNamespace(
        evaluation_id="evaluation-owned", session_id="session-first", epoch=0,
        scene_version=SimpleNamespace(id=suite.metadata["scene"], content_hash="scene-hash"),
    )
    barriers = EvaluationAlertBarriers(service)
    token = barriers.register(suite)["token"]
    barriers.bind(token, session.evaluation_id)
    return barriers, token, service, session, suite, outcome


async def test_only_returns_the_original_graded_outcome_after_release(fixture):
    barriers, token, service, session, suite, outcome = fixture
    close = AsyncMock()

    async def outer_evaluator():
        result = await service._episode(session, suite, {"id": "train-one"})
        await close()
        return result

    task = asyncio.create_task(outer_evaluator())
    await asyncio.sleep(0)
    barriers.original_episode.assert_awaited_once_with(session, suite, {"id": "train-one"})
    assert not task.done() and not close.called
    snapshot = barriers.snapshot(token)
    assert snapshot["state"] == "held"
    assert snapshot["witness"] == {
        "evaluationId": session.evaluation_id, "sessionId": session.session_id,
        "epoch": 0, "outcome": outcome,
    }
    barriers.release(token)
    assert await task is outcome
    close.assert_awaited_once()


async def test_other_evaluations_and_second_case_do_not_wait(fixture):
    barriers, token, service, session, suite, outcome = fixture
    session.evaluation_id = "evaluation-other"
    assert await service._episode(session, suite, {"id": "train-one"}) is outcome
    session.evaluation_id = "evaluation-owned"
    assert await service._episode(session, suite, {"id": "train-two"}) is outcome
    assert barriers.original_episode.await_count == 2
    assert barriers.snapshot(token)["witness"] is None


def test_bind_rejects_changed_pins_and_duplicate_ownership(fixture):
    barriers, token, service, session, suite, _ = fixture
    other = barriers.register(suite)["token"]
    with pytest.raises(HTTPException, match="ownership mismatch"):
        barriers.bind(other, session.evaluation_id)
    service.store.records[session.evaluation_id]["suiteContentHash"] = "changed"
    with pytest.raises(HTTPException, match="ownership mismatch"):
        barriers.bind(token, session.evaluation_id)


async def test_release_before_ready_and_repeated_release_are_safe(fixture):
    barriers, token, service, session, suite, outcome = fixture
    first = barriers.release(token)
    assert barriers.release(token) == first
    assert await service._episode(session, suite, {"id": "train-one"}) is outcome
    assert barriers.snapshot(token)["state"] == "released"
    barriers.original_episode.assert_awaited_once()


async def test_changed_session_pin_cannot_supply_a_held_witness(fixture):
    barriers, token, service, session, suite, _ = fixture
    session.scene_version.content_hash = "changed"
    with pytest.raises(RuntimeError, match="witness mismatch"):
        await service._episode(session, suite, {"id": "train-one"})
    assert barriers.snapshot(token)["witness"] is None
    barriers.original_episode.assert_awaited_once()


async def test_cancellation_propagates_from_the_owned_evaluator_task(fixture):
    barriers, token, service, session, suite, _ = fixture
    task = asyncio.create_task(service._episode(session, suite, {"id": "train-one"}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert barriers.snapshot(token)["state"] == "cancelled"
    assert barriers.release(token)["state"] == "cancelled"
    assert task.done()


async def test_expiry_raises_instead_of_releasing_successfully(fixture, monkeypatch):
    barriers, token, service, session, suite, _ = fixture
    monkeypatch.setattr(barriers, "HOLD_SECONDS", 0)
    with pytest.raises(TimeoutError):
        await service._episode(session, suite, {"id": "train-one"})
    assert barriers.snapshot(token)["state"] == "failed"
    barriers.original_episode.assert_awaited_once()
