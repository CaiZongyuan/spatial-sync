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
