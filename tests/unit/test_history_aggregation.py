import pytest

from packages.info.history import Sample, aggregate, query_window


def samples(values, *, epoch=0):
    return [Sample("/sensor", "temperature", t, value, "rev-one", epoch, 0) for t, value in values]


@pytest.mark.parametrize("agg,expected", [
    ("mean", [[1400, 16 / 3], [1800, 8], [2100, 6]]),
    ("last", [[1400, 10], [1800, 8], [2100, 6]]),
    ("min", [[1400, 2], [1800, 8], [2100, 6]]),
    ("max", [[1400, 10], [1800, 8], [2100, 6]]),
])
def test_aggregates_use_actual_samples_half_open_buckets_and_inclusive_window(agg, expected):
    raw = samples([(999, 900), (1000, 2), (1100, 4), (1400, 10), (1800, 8), (2100, 6), (2101, 900)])
    points, truncated = aggregate(raw, 1000, 2100, 500, agg)
    assert not truncated
    for actual, wanted in zip(points, expected, strict=True):
        assert actual == pytest.approx(wanted)


def test_contexts_never_mix_and_last_keeps_scalar_boolean_null_and_structured_values():
    raw = samples([(1000, "idle"), (1100, False), (1200, None)])
    raw += samples([(1300, {"door": "open"})], epoch=1)
    assert aggregate(raw, 1000, 1500, 1000, "last") == ([[1200, None], [1300, {"door": "open"}]], False)
    with pytest.raises(ValueError, match="numeric"):
        aggregate(samples([(1000, True)]), 1000, 1500, 1000, "mean")


def test_raw_and_aggregated_limits_report_actual_truncation():
    raw = samples([(float(t), t) for t in range(2001)])
    assert aggregate(raw, 0, 2000, None, "last") == ([[float(t), t] for t in range(2000)], True)
    assert aggregate(raw, 0, 1999, None, "last")[1] is False
    assert aggregate(raw, 0, 2000, 1, "mean")[1] is True
    assert aggregate(raw, 0, 2000, 1000, "mean") == ([[999, 499.5], [1999, 1499.5], [2000, 2000]], False)


def test_relative_windows_anchor_to_retained_end_or_explicit_raw_until():
    assert query_window("1h", None, "1m", 4_000_000) == (400_000, 4_000_000, 60_000)
    assert query_window("1s", "2000", None, 9_000_000) == (1000, 2000, None)
    for since, until, every in [("NaN", None, None), ("0", "inf", None), ("2", "1", None),
                                ("0", None, "0s"), ("0", None, "1month"),
                                ("0", "1e308", "0.0000000001ms")]:
        with pytest.raises(ValueError):
            query_window(since, until, every, 2000)


@pytest.mark.parametrize("indexed", [False, True])
def test_unique_and_duplicate_key_batches_keep_measurement_fifo_and_property_time(tmp_path, indexed):
    import json

    from packages.info.history import indexed_samples, scan_samples, write_property_index
    from packages.info.log import FILE_HEADER, Record

    def change(key, rev, value, meta=None):
        return {"key": key, "rev": rev, "values": {"value": value}, "meta": meta or {}}

    def observation(at, changes):
        return {"type": "entity.observations", "sceneRevision": "rev-batch", "epoch": 0,
                "simTime": 0, "sampleTimeMs": at, "source": "virtual", "changes": changes}

    meta = {"simTime": 0, "sampleTimeMs": 1000, "source": "virtual"}
    initial = {"type": "entity.snapshot", "sceneRevision": "rev-batch", "epoch": 0,
               "simTime": 0, "sampleTimeMs": 1000, "invocations": [], "entities": [
                   change("/a", 1, 2, {"value": meta}), change("/b", 1, False, {"value": meta})]}
    messages = [
        initial,
        observation(1100, [change("/a", 2, 4), change("/b", 2, 10)]),
        observation(1200, [change("/a", 3, 999, {"value": meta | {"sampleTimeMs": 900}}),
                           change("/b", 3, 11)]),
        observation(1300, [change("/a", 4, 5), change("/a", 3, 777),
                           change("/a", 5, 6), change("/a", 6, 5)]),
    ]
    raw = FILE_HEADER + b"".join(
        Record(2, message["sampleTimeMs"], json.dumps(message).encode()).encode() for message in messages
    )
    path = tmp_path / "seg-000001.bin"
    path.write_bytes(raw)
    if indexed:
        sidecar = path.with_name(write_property_index(path))
        a = indexed_samples(raw, sidecar, "/a", "value", 0, 2000)
        b = indexed_samples(raw, sidecar, "/b", "value", 0, 2000)
    else:
        actual = list(scan_samples(raw))
        a, b = ([sample for sample in actual if sample.key == key] for key in ("/a", "/b"))
    assert [(sample.t_ms, sample.value) for sample in a] == [
        (1000, 2), (1100, 4), (1300, 5), (1300, 6), (1300, 5)]
    assert [(sample.t_ms, sample.value) for sample in b] == [(1000, False), (1100, 10), (1200, 11)]
    assert b[0].value is False
