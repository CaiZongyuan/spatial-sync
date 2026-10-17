import asyncio
import subprocess
import threading
from types import SimpleNamespace

import psutil
import pytest

from apps.api.app import platform_monitor as monitor_module
from apps.api.app.platform_monitor import HostSampler, PlatformMonitor, platform_snapshot, unavailable_system


def row(*, state="PAUSED", clients=0, alive=True, pid=123):
    return {
        "sessionId": "owned", "source": "newton", "state": state, "connectedClients": clients,
        "backend": {"alive": alive, "pid": pid, "cpuPercent": None, "memoryBytes": None,
                    "metricsReason": None},
    }


def test_sampler_uses_owned_pids_and_preserves_unknown_metrics(monkeypatch):
    requested = []

    class Process:
        def __init__(self, pid):
            requested.append(pid)

        def is_running(self):
            return True

        def memory_info(self):
            return SimpleNamespace(rss=123456)

        def cpu_percent(self, interval):
            return 175.5

    monkeypatch.setattr(psutil, "Process", Process)
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval: 42.5)
    monkeypatch.setattr(psutil, "cpu_count", lambda: 8)
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: SimpleNamespace(total=1000, available=400, percent=60)
    )
    monkeypatch.setattr(HostSampler, "_gpus", lambda self: ([], "unsupported", "No supported driver"))
    sampler = HostSampler()
    first = sampler.sample([row(clients=2), row(state="STOPPED", alive=False, pid=456)])
    assert first["summary"] == {
        "totalSessions": 2, "runningSessions": 0, "pausedSessions": 1,
        "connectedSessions": 1, "connectedClients": 2, "newtonBackends": 1,
    }
    assert first["system"]["cpu"]["percent"] is None
    assert first["system"]["cpu"]["reason"]
    assert first["sessions"][0]["backend"]["cpuPercent"] is None
    assert first["sessions"][1]["backend"]["memoryBytes"] is None
    second = sampler.sample([row()])
    assert second["system"]["cpu"]["percent"] == 42.5
    assert second["sessions"][0]["backend"]["cpuPercent"] == 175.5
    assert second["sessions"][0]["backend"]["memoryBytes"] == 123456
    assert requested == [123]
    sampler.sample([])
    assert not sampler._processes


def test_process_and_system_failures_remain_null(monkeypatch):
    def denied(*args, **kwargs):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "Process", denied)
    monkeypatch.setattr(psutil, "cpu_percent", denied)
    monkeypatch.setattr(psutil, "virtual_memory", denied)
    monkeypatch.setattr(HostSampler, "_gpus", lambda self: ([], "unavailable", "Driver failed"))
    result = HostSampler().sample([row()])
    assert result["system"]["cpu"]["percent"] is None
    assert result["system"]["memory"]["usedBytes"] is None
    backend = result["sessions"][0]["backend"]
    assert backend["cpuPercent"] is None and backend["memoryBytes"] is None
    assert "unavailable" in backend["metricsReason"]


def test_runner_that_exits_during_sample_does_not_count_as_a_live_backend(monkeypatch):
    def exited(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", exited)
    monkeypatch.setattr(HostSampler, "_gpus", lambda self: ([], "unsupported", "No supported driver"))
    result = HostSampler().sample([row()])
    assert result["summary"]["newtonBackends"] == 0
    backend = result["sessions"][0]["backend"]
    assert backend["pid"] == 123 and backend["alive"] is False
    assert backend["viewer"]["status"] == "unavailable" and backend["viewer"]["url"] is None


def test_nvidia_query_converts_real_units_and_marks_unknowns(monkeypatch):
    monkeypatch.setattr(monitor_module.shutil, "which", lambda name: "owned-nvidia-smi")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="0, Real GPU, 17, 100, 8000, 55\n1, Other GPU, N/A, 2, 10, N/A\n")

    monkeypatch.setattr(monitor_module.subprocess, "run", run)
    gpus, status, reason = HostSampler._gpus()
    assert status == "available" and reason
    assert gpus[0]["memoryUsedBytes"] == 100 * 1024 * 1024
    assert gpus[0]["temperatureC"] == 55
    assert gpus[1]["utilizationPercent"] is None and gpus[1]["temperatureC"] is None
    assert calls[0][1]["timeout"] == 1 and calls[0][1]["check"] is True


@pytest.mark.parametrize("failure", ["missing", "timeout", "invalid"])
def test_nvidia_failures_are_explained(monkeypatch, failure):
    monkeypatch.setattr(monitor_module.shutil, "which", lambda name: None if failure == "missing" else "smi")

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("smi", 1)
        return SimpleNamespace(stdout="invalid")

    monkeypatch.setattr(monitor_module.subprocess, "run", run)
    gpus, status, reason = HostSampler._gpus()
    assert gpus == [] and reason
    assert status == ("unsupported" if failure == "missing" else "unavailable")


@pytest.mark.parametrize("cancel_close", [False, True])
async def test_lifespan_owns_one_worker_and_waits_for_inflight_sample(monkeypatch, cancel_close):
    monkeypatch.setattr(monitor_module, "INTERVAL_SECONDS", 0.01)
    started, release = threading.Event(), threading.Event()
    thread_ids = []

    class Sampler:
        def sample(self, rows):
            thread_ids.append(threading.get_ident())
            started.set()
            assert release.wait(2)
            return platform_snapshot(rows, unavailable_system("fake"))

    manager = SimpleNamespace(platform_sessions=lambda: [])
    monitor = PlatformMonitor(manager, sampler=Sampler())
    await monitor.start()
    await monitor.start()
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.001)
    assert started.is_set()
    closing = asyncio.create_task(monitor.close())
    await asyncio.sleep(0.01)
    assert not closing.done()
    if cancel_close:
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
    release.set()
    if cancel_close:
        with pytest.raises(asyncio.CancelledError):
            await closing
    else:
        await closing
    assert len(thread_ids) == 1 and monitor._task is None and monitor._worker is None
    assert all(thread.ident not in thread_ids for thread in threading.enumerate())


async def test_sampling_recovers_after_failure(monkeypatch):
    monkeypatch.setattr(monitor_module, "INTERVAL_SECONDS", 0.01)
    calls = []

    class Sampler:
        def sample(self, rows):
            calls.append(threading.get_ident())
            if len(calls) == 1:
                raise RuntimeError("injected probe failure")
            return platform_snapshot(rows, unavailable_system("recovered"))

    monitor = PlatformMonitor(SimpleNamespace(platform_sessions=lambda: []), sampler=Sampler())
    await monitor.start()
    try:
        for _ in range(100):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.002)
        assert len(calls) >= 2
        assert monitor.snapshot["system"]["gpuMessage"] == "recovered"
        assert len(set(calls)) == 1 and calls[0] != threading.get_ident()
    finally:
        await monitor.close()
