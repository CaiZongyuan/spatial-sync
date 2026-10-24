from datetime import UTC, datetime, timedelta

import pytest

from packages.cost.ranges import reporting_window
from packages.info.plans import PlanError


@pytest.mark.parametrize(
    "key,seconds,bucket",
    [("1h", 3600, 300), ("1d", 86400, 3600), ("7d", 604800, 86400), ("30d", 2592000, 86400)],
)
def test_cost_presets_and_partial_boundary_buckets_cover_the_exact_window(key, seconds, bucket):
    window = reporting_window(key, 2_000_000_123)
    bounds = list(window.buckets())
    assert window.end - window.start == seconds and window.bucket_seconds == bucket
    assert bounds[0][0] == window.start and bounds[-1][1] == window.end
    assert sum(end - start for start, end in bounds) == seconds
    assert all(first[1] == second[0] for first, second in zip(bounds, bounds[1:]))


@pytest.mark.parametrize("days,bucket", [(92, 86400), (93, 604800)])
def test_custom_cost_window_changes_from_daily_to_weekly_after_92_days(days, bucket):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    window = reporting_window(
        "custom", 0, start=start.isoformat(), end=(start + timedelta(days=days)).isoformat()
    )
    assert window.bucket_seconds == bucket


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-01-01", "2026-01-02"),
        ("invalid", "2026-01-02T00:00:00Z"),
        ("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z"),
    ],
)
def test_custom_cost_window_rejects_ambiguous_or_reversed_dates(start, end):
    with pytest.raises(PlanError):
        reporting_window("custom", 0, start=start, end=end)
