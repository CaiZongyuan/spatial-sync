"""Cumulative changes relative to one declared full keyframe, never the previous delta."""

from dataclasses import dataclass

from packages.protocol.schema import TransformFrame


@dataclass(frozen=True)
class DeltaPayload:
    base_seq: int
    poses: list
    removed_stream_ids: list[int]


class DeltaBaseline:
    def __init__(self, frame: TransformFrame):
        self.frame = frame
        self.poses = {row[0]: row for row in frame.poses}

    def difference(self, frame: TransformFrame) -> DeltaPayload:
        if frame.sceneRevision != self.frame.sceneRevision or frame.epoch != self.frame.epoch:
            raise ValueError("delta requires a keyframe in the same revision and epoch")
        if frame.seq <= self.frame.seq:
            raise ValueError("delta sequence must follow its keyframe")
        current_ids = {row[0] for row in frame.poses}
        changed = [row for row in frame.poses if self.poses.get(row[0]) != row]
        removed = [stream_id for stream_id in self.poses if stream_id not in current_ids]
        return DeltaPayload(self.frame.seq, changed, removed)
