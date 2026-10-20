"""Real Synthetic telemetry fixture: one sensor deliberately stops publishing humidity."""

from approval_gateway import app

from apps.api.app.sources import synthetic_source
from tests.fixtures.live_program import QuietHumiditySensor

__all__ = ["app"]


synthetic_source.REGISTRY = synthetic_source.REGISTRY | {QuietHumiditySensor.id: QuietHumiditySensor}
