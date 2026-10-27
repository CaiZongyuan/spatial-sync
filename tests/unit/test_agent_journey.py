"""The I5 runner accepts only a causally complete I3 journey."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import agent_journey
from tools.agent_journey import TUBES, Observer, Owned, Run, journey_order, viewer_confirmed

ROBOT, CENTRIFUGE = "/World/Lab/RobotA", "/World/Lab/Centrifuge"


@pytest.mark.parametrize("phase,rpm", [("decelerating", 0), ("accelerating", 0), ("idle", 1000)])
async def test_pending_rotor_checks_wait_for_idle_and_zero_speed(monkeypatch, phase, rpm):
    clock = SimpleNamespace(now=0.0)
    stop = asyncio.Event()

    async def sleep(seconds):
        clock.now += seconds
        if clock.now >= 2:
            stop.set()

    monkeypatch.setattr(agent_journey, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(agent_journey, "asyncio", SimpleNamespace(sleep=sleep))
    observer = Observer(None, "/owned", None)
    slots = {key: f"{CENTRIFUGE}/Rotor/Slot{index * 2 - 1}" for key, index in TUBES.items() if index < 3}

    async def values():
        entities = {key: {"location": slots.get(key, f"/World/Lab/Rack/A{index}")}
                    for key, index in TUBES.items()}
        entities[CENTRIFUGE] = {"phase": phase if clock.now < 1 else "idle",
                               "rpm": rpm if clock.now < 1 else 0, "lidOpen": False}
        return entities

    checked = []

    async def check(key, expected):
        if key in slots:
            checked.append((key, (await values())[CENTRIFUGE]))
        return True

    monkeypatch.setattr(observer, "values", values)
    monkeypatch.setattr(observer, "check", check)
    await observer.watch(stop)

    assert [key for key, state in checked] == list(slots)
    assert all(state["phase"] == "idle" and state["rpm"] == 0 for key, state in checked)


def invocation(key, action, params=None, status="succeeded"):
    return {"entityKey": key, "action": action, "params": params or {}, "status": status}


def pick_place(source, target):
    return invocation(ROBOT, "pickPlace", {"from": source, "to": target})


JOURNEY = [
    pick_place("rack.A1", "centrifuge.slot1"),
    pick_place("rack.A2", "centrifuge.slot3"),
    invocation(ROBOT, "home"),
    invocation(CENTRIFUGE, "closeLid"),
    invocation(CENTRIFUGE, "start", {"rpm": 3000, "durationSeconds": 20}),
    invocation(CENTRIFUGE, "openLid"),
    pick_place("centrifuge.slot1", "rack.A1"),
    pick_place("centrifuge.slot3", "rack.A2"),
]


def test_complete_journey_with_a_rejection_is_ordered():
    rejected = invocation(CENTRIFUGE, "closeLid", status="rejected")
    result = journey_order([*JOURNEY[:2], rejected, *JOURNEY[2:]])
    assert result["ordered"]
    assert result["steps"]["closeLid"] == 3


def test_spinning_before_the_lid_closes_is_not_ordered():
    assert not journey_order([*JOURNEY[:2], JOURNEY[4], JOURNEY[3], *JOURNEY[5:]])["ordered"]


def test_a_missing_return_is_not_ordered():
    assert not journey_order(JOURNEY[:-1])["ordered"]


def test_other_parameters_or_devices_do_not_count():
    slow = invocation(CENTRIFUGE, "start", {"rpm": 500, "durationSeconds": 20})
    assert not journey_order([*JOURNEY[:4], slow, *JOURNEY[5:]])["ordered"]
    elsewhere = invocation("/World/Lab/Other", "closeLid")
    assert not journey_order([*JOURNEY[:3], elsewhere, *JOURNEY[4:]])["ordered"]


async def test_failed_signal_retains_the_owned_process(monkeypatch):
    owned = Owned()
    process = SimpleNamespace(pid=123, returncode=None, wait=AsyncMock())
    owned.processes["api"] = process

    def denied(*args):
        raise PermissionError("cannot signal owned group")

    monkeypatch.setattr("tools.agent_journey.os.killpg", denied)
    with pytest.raises(PermissionError):
        await owned.stop("api")
    assert owned.processes == {"api": process}
    process.wait.assert_not_called()


async def test_stopping_all_attempts_later_processes_after_a_failure(monkeypatch):
    owned = Owned()
    owned.processes = {"api": SimpleNamespace(pid=123), "web": SimpleNamespace(pid=456)}
    attempted = []

    async def stop(name):
        attempted.append(name)
        if name == "api":
            raise PermissionError("cannot signal owned group")
        del owned.processes[name]

    monkeypatch.setattr(owned, "stop", stop)
    errors = await owned.stop_all()
    assert attempted == ["api", "web"]
    assert list(owned.processes) == ["api"]
    assert errors == [{"name": "api", "pid": 123, "error": "PermissionError('cannot signal owned group')"}]


async def test_a_process_is_forgotten_only_after_its_group_is_gone(monkeypatch):
    owned = Owned()
    process = SimpleNamespace(pid=123, returncode=0, wait=AsyncMock())
    owned.processes["api"] = process

    def gone(*args):
        raise ProcessLookupError

    monkeypatch.setattr("tools.agent_journey.os.killpg", gone)
    assert await owned.stop("api") == 0
    assert not owned.processes
    process.wait.assert_awaited_once()


async def test_an_unkilled_group_keeps_its_identity(monkeypatch):
    owned = Owned()
    process = SimpleNamespace(pid=123, returncode=0, wait=AsyncMock())
    owned.processes["api"] = process
    monkeypatch.setattr("tools.agent_journey.os.killpg", lambda *args: None)
    with pytest.raises(RuntimeError, match="123"):
        await owned.stop("api", grace=0)
    assert owned.processes == {"api": process}


@pytest.mark.parametrize("failed", [("stop",), ("close",), ("stop", "close")])
async def test_final_cleanup_errors_still_write_a_failure_receipt(monkeypatch, tmp_path, failed):
    run = Run(SimpleNamespace(output=tmp_path))
    stop = AsyncMock(side_effect=OSError("stop failed") if "stop" in failed else None, return_value=[])
    close = AsyncMock(side_effect=OSError("close failed") if "close" in failed else None)
    run.client = SimpleNamespace(aclose=close)
    monkeypatch.setattr(run, "start_lab", AsyncMock())
    monkeypatch.setattr(run, "journey", AsyncMock())
    monkeypatch.setattr(run, "cleanup", AsyncMock())
    monkeypatch.setattr(run.owned, "stop_all", stop)
    result = await run.execute()
    receipt = json.loads((run.output / "receipt.json").read_text())
    assert receipt == result
    for name in failed:
        assert any(f"{name} failed" in failure for failure in receipt["failures"])
    stop.assert_awaited_once()
    close.assert_awaited_once()


async def test_remaining_owned_identity_is_in_the_failure_receipt(monkeypatch, tmp_path):
    run = Run(SimpleNamespace(output=tmp_path))
    run.owned.processes["api"] = SimpleNamespace(pid=123)
    for method in ("start_lab", "journey", "cleanup"):
        monkeypatch.setattr(run, method, AsyncMock())
    error = {"name": "api", "pid": 123, "error": "PermissionError('denied')"}
    monkeypatch.setattr(run.owned, "stop_all", AsyncMock(return_value=[error]))
    receipt = await run.execute()
    assert receipt["cleanupErrors"] == [error]
    assert receipt["remainingProcesses"] == [{"name": "api", "pid": 123}]
    assert receipt["failures"]


def test_viewer_requires_resting_pose_coverage_for_each_tube():
    report = {
        "sessionId": "owned",
        "failures": [],
        "locationComparisons": 12,
        "poseComparisons": 12,
        "poseComparisonsByTube": {key: 3 for key in TUBES},
    }
    assert viewer_confirmed(report, "owned")
    assert not viewer_confirmed(report, "other")
    report["poseComparisonsByTube"][next(iter(TUBES))] = 0
    assert not viewer_confirmed(report, "owned")
    del report["poseComparisonsByTube"]
    assert not viewer_confirmed(report, "owned")
