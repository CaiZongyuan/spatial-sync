"""Normal taught motion stays above the visible tabletop and reaches the same Sites."""

import math
import uuid
from itertools import product

import pytest

from apps.api.app.sources.synthetic_source import CENTRIFUGE, ROBOT, SyntheticSource
from packages.protocol.math import compose_pose, rotate_vector
from packages.protocol.schema import Command, InvokeRequest, SessionOptions

LINKS = ("/World/Lab/Robot/Link1", "/World/Lab/Robot/Link2", ROBOT + "/Hand")


def lowest_robot_mesh(manifest, frame):
    poses = {row[0]: {"p": row[1:4], "q": row[4:8]} for row in frame.poses}
    minimum = float("inf")
    for node in manifest.nodes:
        if node.entityKey not in LINKS:
            continue
        for visual in node.visuals:
            world = compose_pose(poses[node.streamId], visual.localPose.model_dump())
            geometry = visual.geometry
            if geometry.type == "capsule":
                axis = rotate_vector(world["q"], [0, 0, geometry.halfHeight])
                bottom = world["p"][2] - abs(axis[2]) - geometry.radius
            else:
                assert geometry.type == "box"
                bottom = min(
                    world["p"][2] + rotate_vector(world["q"], corner)[2]
                    for corner in product(*[(-half, half) for half in geometry.halfExtents])
                )
            minimum = min(minimum, bottom)
    return minimum


async def run_action(source, manifest, action, **params):
    token = uuid.uuid4().hex
    result = await source.invoke(
        InvokeRequest(
            invocationId=token,
            requestId=token,
            entityKey=ROBOT,
            action=action,
            params=params,
            epoch=0,
            createdSimTime=source.sim_time,
            createdAtMs=0,
        )
    )
    assert result.status == "accepted", result.model_dump()
    phase = source.host.reader(ROBOT, "phase")
    for tick in range(1200):
        step = await source.execute(Command(requestId=f"{token}-{tick}", command="step", args={"count": 4}))
        # Test the geometry actually published at every control tick, including transport between Sites.
        assert lowest_robot_mesh(manifest, step.frame) >= 0.75, (action, params, source.sim_time)
        if phase() == "idle":
            terminal = next(
                item for item in source.entity_snapshot().invocations if item.invocationId == token
            )
            assert terminal.status == "succeeded", terminal.model_dump()
            return step.frame
    raise AssertionError("Taught action exceeded its bounded expected duration")


async def test_all_taught_approach_and_grasp_paths_from_home_clear_table_and_reach_unchanged_sites():
    source = SyntheticSource("visible-waypoints")
    try:
        manifest, catalog = await source.start("lab-demo", SessionOptions())
        paused = await source.execute(Command(requestId="pause-visible", command="pause"))
        assert lowest_robot_mesh(manifest, paused.frame) >= 0.75
        locations = catalog.entities[ROBOT]["config"]["locations"]["locations"]
        expected = {
            **{
                f"rack.{row}{col}": [0.55 + (col - 1) * 0.1, y, 0.82]
                for row, y in (("A", -0.3), ("B", -0.1))
                for col in range(1, 5)
            },
            **{
                f"centrifuge.slot{index}": position
                for index, position in enumerate(
                    ([-0.4, 0.4, 0.95], [-0.5, 0.5, 0.95], [-0.6, 0.4, 0.95], [-0.5, 0.3, 0.95]), 1
                )
            },
        }
        assert set(locations) == set(expected)
        for name, location in locations.items():
            assert location["p"] == pytest.approx(expected[name], rel=0, abs=1e-12)
            for pose, height in (("approach", 0.12), ("grasp", 0)):
                await run_action(source, manifest, "home")
                frame = await run_action(source, manifest, "moveTo", location=name, pose=pose)
                tcp = source.host.reader(ROBOT, "tcpPose")()
                assert math.dist(tcp[:3], [*expected[name][:2], expected[name][2] + height]) <= 0.01
                hand = next(row[1:] for row in frame.poses if row[0] == 3)
                assert hand == pytest.approx(tcp, rel=0, abs=1e-12)
    finally:
        await source.close()


async def test_actual_pick_place_paths_keep_links_visible_and_tube_locations_match_sites():
    source = SyntheticSource("visible-transfer")
    try:
        manifest, _ = await source.start("lab-demo", SessionOptions())
        await source.execute(Command(requestId="pause-transfer", command="pause"))
        for index, slot in ((1, 1), (2, 3)):
            await run_action(
                source, manifest, "pickPlace", **{"from": f"rack.A{index}", "to": f"centrifuge.slot{slot}"}
            )
            await run_action(source, manifest, "home")
            tube = f"/World/Lab/Tubes/Tube{index:02d}"
            assert source.host.reader(tube, "location")() == CENTRIFUGE + f"/Rotor/Slot{slot}"
    finally:
        await source.close()
