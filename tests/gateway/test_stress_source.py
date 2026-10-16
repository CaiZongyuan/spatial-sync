import pytest

from apps.api.app.sources.stress_scene import STRESS_SCENES, poses, stream_id
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.schema import Command, SessionOptions


@pytest.mark.parametrize("scene_id,counts", STRESS_SCENES.items())
async def test_real_stress_source_counts_complete_non_contiguous_rows_and_commands(scene_id, counts):
    source = SyntheticSource("load")
    try:
        manifest = await source.start(scene_id, SessionOptions())
        moving, static = counts
        assert sum(node.kind == "body" for node in manifest.nodes) == moving
        assert sum(node.kind == "static" for node in manifest.nodes) == static
        initial = source.latest_frame()
        assert len(initial.poses) == moving
        assert {row[0] for row in initial.poses} == {stream_id(index) for index in range(moving)}
        assert initial.poses[1][0] == stream_id(7)
        result = await source.execute(Command(requestId="pause", command="pause"))
        assert result.ack.status == "applied"
        stepped = await source.execute(Command(requestId="step", command="step", args={"count": 3}))
        assert stepped.frame.poses == poses(stepped.frame.simTime, moving, static)
        reset = await source.execute(Command(requestId="reset", command="reset"))
        assert reset.frame.epoch == 1 and reset.frame.simTime == 0
        assert reset.frame.poses == initial.poses
    finally:
        await source.close()
