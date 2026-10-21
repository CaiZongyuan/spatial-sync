"""Optional Native execution and independent budget reporting stay explicit."""

import errno
import socket
import sys
from types import SimpleNamespace

import pytest

from tools.bench_info import postphases


@pytest.mark.parametrize("include_newton", [False, True])
async def test_postphases_keep_native_optional_and_complete_before_log(monkeypatch, tmp_path, include_newton):
    from tools import bench_newton_info, bench_recording

    order = []

    async def native(**kwargs):
        order.append("native-complete")
        assert kwargs == {"python": None, "port": 8011}
        return {"status": "unavailable", "reason": "i3_not_integrated"}

    async def log(directory):
        assert directory == tmp_path / "session-log-benchmark"
        assert order == (["native-complete"] if include_newton else [])
        order.append("log-complete")
        return {"meetsThroughputBudget": False}

    monkeypatch.setattr(bench_newton_info, "probe_newton_info", native)
    monkeypatch.setattr(bench_recording, "benchmark_log", log)
    result = await postphases(
        SimpleNamespace(include_newton=include_newton, newton_python=None, port=8011), tmp_path
    )
    assert result["newton"]["status"] == "unavailable"
    assert result["newton"]["reason"] == (
        "i3_not_integrated" if include_newton else "not_requested; use --include-newton"
    )
    assert result["logThroughput"]["status"] == "measured"
    assert result["logThroughput"]["budgets"] == {"throughput5MBps": False}
    assert order[-1] == "log-complete"


async def test_log_correctness_failure_is_invalid(monkeypatch, tmp_path):
    from tools import bench_recording

    async def log(directory):
        raise AssertionError("missing recorded frame")

    monkeypatch.setattr(bench_recording, "benchmark_log", log)
    result = await postphases(SimpleNamespace(include_newton=False), tmp_path)
    assert result["logThroughput"]["status"] == "invalid"
    assert result["logThroughput"]["error"] == "AssertionError: missing recorded frame"
    assert "budgets" not in result["logThroughput"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux is the benchmark reference platform")
async def test_native_postphase_reuses_a_closed_port_with_time_wait(monkeypatch, tmp_path):
    from tools import bench_newton_info, bench_recording

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with socket.create_connection(listener.getsockname()) as client:
            accepted, _ = listener.accept()
            with accepted:
                accepted.shutdown(socket.SHUT_WR)
                assert client.recv(1) == b""
    # No listener remains, but the closed server connection still owns TIME_WAIT.
    with socket.socket() as ordinary_probe, pytest.raises(OSError) as collision:
        ordinary_probe.bind(("127.0.0.1", port))
    assert collision.value.errno == errno.EADDRINUSE

    eventually = bench_newton_info.eventually

    class GatewayReadyProbe(Exception):
        pass

    async def stop_after_real_gateway_health(predicate, timeout):
        assert await eventually(predicate, timeout=timeout)
        raise GatewayReadyProbe("owned gateway served health on the reused port")

    async def log(directory):
        return {"meetsThroughputBudget": True}

    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(bench_newton_info, "eventually", stop_after_real_gateway_health)
    monkeypatch.setattr(bench_recording, "benchmark_log", log)
    result = await postphases(SimpleNamespace(include_newton=True, newton_python=None, port=port), tmp_path)
    native = result["newton"]
    assert native["error"] == "GatewayReadyProbe: owned gateway served health on the reused port"
    assert native["execution"]["gatewayExited"]
    assert result["logThroughput"]["status"] == "measured"


async def test_native_postphase_still_rejects_an_active_listener(tmp_path):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(OSError) as collision:
            await postphases(
                SimpleNamespace(include_newton=True, newton_python=None, port=listener.getsockname()[1]),
                tmp_path,
            )
        assert collision.value.errno == errno.EADDRINUSE
