"""Source/gateway coordination values; these are not additional WebSocket messages."""

from typing import Literal

from packages.protocol.schema import (
    CommandResult,
    JointState,
    Model,
    NonEmpty,
    NonNegative,
    SessionOptions,
    UInt32,
)


class InformationClock(Model):
    """Ordered host-tick watermark on the reliable internal source stream only."""

    type: Literal["information.clock"] = "information.clock"
    sceneRevision: NonEmpty
    epoch: UInt32
    simTime: NonNegative
    sampleTimeMs: NonNegative


__all__ = ["CommandResult", "InformationClock", "JointState", "SessionOptions"]
