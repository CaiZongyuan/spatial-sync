import pytest

from apps.api.app.delta_stream import DeltaBaseline
from packages.protocol.schema import TransformFrame


def frame(seq, rows, **changes):
    return TransformFrame(
        sessionId="s",
        sceneRevision="r",
        epoch=0,
        seq=seq,
        simTime=seq / 30,
        sampleTimeMs=seq * 1000 / 30,
        poses=[(key, x, 0, 0, 0, 0, 0, 1) for key, x in rows],
        **changes,
    )


def test_dropping_middle_delta_and_return_to_base_are_recoverable_without_previous_delta():
    base = DeltaBaseline(frame(10, [(1, 0), (2, 5)]))
    first = base.difference(frame(11, [(1, 1), (2, 5), (3, 8)]))
    assert [(row[0], row[1]) for row in first.poses] == [(1, 1), (3, 8)]
    # Delta 11 can be dropped. Body 1 returned to its base pose and new Body 3 left.
    last = base.difference(frame(12, [(1, 0)]))
    assert last.base_seq == 10 and last.poses == [] and last.removed_stream_ids == [2]
    # Reconstructing from base leaves exactly Body 1 at 0, not its old delta pose at 1.
    assert {key: row[1] for key, row in base.poses.items() if key not in last.removed_stream_ids} == {1: 0}


def test_new_members_are_repeated_until_rebase_and_cannot_be_lost_by_latest_only_overwrite():
    base = DeltaBaseline(frame(1, [(1, 0)]))
    for seq in (2, 3):
        delta = base.difference(frame(seq, [(1, 0), (2, 9)]))
        assert [(row[0], row[1]) for row in delta.poses] == [(2, 9)]
    with pytest.raises(ValueError, match="revision"):
        base.difference(frame(4, [(1, 0)]).model_copy(update={"epoch": 1}))
