from packages.training.node_telemetry import NodeTelemetryHistory


def gpu(identity="GPU-A", **values):
    return {"id": identity, "utilization": 40.0, "memoryUsedMb": 2048.0, **values}


def test_history_retains_observed_zero_and_missing_samples_without_filling_gaps():
    history = NodeTelemetryHistory()
    history.observe("local", "boot", [gpu(observedAt=100.0, utilization=0.0)], 101.0)
    history.observe("local", "boot", [gpu(observedAt=120.0, utilization=None,
                                          unavailableReason="Driver did not report utilization")], 121.0)
    history.observe("local", "boot", [gpu(observedAt=None)], 141.0)
    result = history.for_gpu("local", "boot", "GPU-A", 141.0)
    assert [sample["at"] for sample in result["samples"]] == [100.0, 120.0, 141.0]
    assert [sample["utilization"] for sample in result["samples"]] == [0.0, None, None]
    assert result["samples"][1]["unavailableReason"] == "Driver did not report utilization"
    assert result["samples"][2]["observedAt"] is None
    assert result["samples"][2]["memoryUsedMb"] is None
    assert result["samples"][2]["unavailableReason"]


def test_history_buckets_expire_and_cannot_cross_node_gpu_or_producer_identity():
    history = NodeTelemetryHistory()
    history.observe("local", "boot", [gpu(observedAt=100.0), gpu("GPU-B", observedAt=100.0)], 100.0)
    history.observe("local", "boot", [gpu(observedAt=105.0, utilization=70.0),
                                       gpu("GPU-B", observedAt=100.0)], 105.0)
    history.observe("local", "boot", [gpu(observedAt=103.0)], 106.0)
    samples = history.for_gpu("local", "boot", "GPU-A", 106.0)["samples"]
    assert len(samples) == 1 and samples[0]["utilization"] == 70.0
    assert history.for_gpu("local", "boot", "GPU-B", 106.0)["samples"] == []
    assert history.for_gpu("other", "boot", "GPU-A", 106.0)["samples"] == []
    assert history.for_gpu("local", "boot", "GPU-A", 3706.0)["samples"] == []
    history.observe("local", "new-boot", [gpu(observedAt=3707.0, utilization=10.0)], 3707.0)
    assert history.for_gpu("local", "boot", "GPU-A", 3707.0)["samples"] == []
    assert history.for_gpu("local", "new-boot", "GPU-A", 3707.0)["samples"][0]["utilization"] == 10.0


def test_invalid_timestamps_are_explicit_gaps_and_window_storage_is_bounded():
    history = NodeTelemetryHistory()
    for second in range(4000):
        history.observe("local", "boot", [gpu(observedAt=float(second))], float(second))
    result = history.for_gpu("local", "boot", "GPU-A", 3999.0)
    assert len(result["samples"]) <= 361
    assert result["samples"][0]["at"] >= 399.0
    history.observe("local", "boot", [gpu(observedAt=5000.0)], 4000.0)
    sample = history.for_gpu("local", "boot", "GPU-A", 4000.0)["samples"][-1]
    assert sample["at"] == 4000.0 and sample["observedAt"] is None
    assert sample["utilization"] is None and sample["unavailableReason"]
