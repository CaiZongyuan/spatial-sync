"""Opt-in WS extensions. Runner IPC and legacy WS kinds 1/2 retain their 44-byte header."""

import struct

from packages.protocol.schema import TransformDelta
from packages.transport import HEADER, POSE

SCOPE = struct.Struct("<I")
DELTA = struct.Struct("<IQI")


def encode_scoped_frame(frame, revision_numeric_id: int) -> bytes:
    delta = isinstance(frame, TransformDelta)
    kind = 3 if delta else 5 if frame.type == "stream.snapshot" else 4
    header = HEADER.pack(
        b"NSYN",
        1,
        kind,
        revision_numeric_id,
        frame.epoch,
        frame.seq,
        frame.simTime,
        frame.sampleTimeMs,
        len(frame.poses),
    )
    extension = (
        DELTA.pack(frame.subscriptionId, frame.baseSeq, len(frame.removedStreamIds))
        if delta
        else SCOPE.pack(frame.subscriptionId)
    )
    body = b"".join(POSE.pack(*row) for row in frame.poses)
    removals = b"".join(SCOPE.pack(stream_id) for stream_id in frame.removedStreamIds) if delta else b""
    return header + extension + body + removals
