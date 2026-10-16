"""Optional Native execution and independent budget reporting stay explicit."""

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
