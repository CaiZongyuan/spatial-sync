"""UTC reporting windows; interval allocation preserves cumulative GPU seconds."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from packages.info.plans import PlanError

DAY = 86400
PRESETS = {"1h": (3600, 300), "1d": (DAY, 3600), "7d": (7 * DAY, DAY), "30d": (30 * DAY, DAY)}


@dataclass(frozen=True)
class CostWindow:
    key: str
    start: float
    end: float
    bucket_seconds: int

    def document(self):
        return {
            "key": self.key,
            "start": self.start,
            "end": self.end,
            "bucketSeconds": self.bucket_seconds,
            "timezone": "UTC",
        }

    def buckets(self):
        at = math.floor(self.start / self.bucket_seconds) * self.bucket_seconds
        while at < self.end:
            yield max(at, self.start), min(at + self.bucket_seconds, self.end)
            at += self.bucket_seconds


def reporting_window(key, now, *, start=None, end=None, earliest=None):
    if key in PRESETS:
        seconds, bucket = PRESETS[key]
        return CostWindow(key, now - seconds, now, bucket)
    if key == "today":
        return CostWindow(key, math.floor(now / DAY) * DAY, now, 3600)
    if key == "all":
        begin = min(earliest if earliest is not None else now - DAY, now)
        return CostWindow(key, begin, now, DAY if now - begin <= 92 * DAY else 7 * DAY)
    if key == "custom":
        try:
            values = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (start, end)]
            if any(value.tzinfo is None for value in values):
                raise ValueError("Timezone is required")
            begin, finish = [value.astimezone(UTC).timestamp() for value in values]
            if not math.isfinite(begin + finish) or begin >= finish:
                raise ValueError("Start must precede end")
            return CostWindow(key, begin, finish, DAY if finish - begin <= 92 * DAY else 7 * DAY)
        except (ValueError, AttributeError, TypeError, OverflowError):
            pass
    raise PlanError(
        "invalid_cost_range",
        "Use 1h, 1d, 7d, 30d, today, all or custom with timezone-qualified start/end",
        status=422,
    )


def overlap_fraction(entry, start, end, *, include_end=True):
    begin, finish = entry["startAt"], entry["endAt"]
    if finish <= begin:
        return float(start <= finish < end or include_end and start <= finish == end)
    return max(0, min(finish, end) - max(begin, start)) / (finish - begin)
