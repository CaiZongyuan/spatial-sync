"""Registered, reproducible scene builders. Arbitrary paths are never accepted by the API."""

from collections.abc import Callable
from dataclasses import dataclass
from math import pi
from typing import Any


@dataclass(frozen=True)
class SceneConfig:
    builder: Callable[[], Any]
    solver: str = "xpbd"
    driver_factory: Callable[[Any], Any] | None = None
    warmup_steps: int = 1


def falling_boxes_builder():
    import newton
    import warp as wp

    builder = newton.ModelBuilder(up_axis=newton.Axis.Z)
    builder.add_ground_plane(label="World/Floor")

    box = newton.ModelBuilder()
    body = box.add_body(xform=wp.transform((0, 0, 1.5), wp.quat_identity()), label="Box")
    box.add_shape_box(body, hx=0.15, hy=0.2, hz=0.15, label="Box/geometry", color=(0.2, 0.55, 0.9))
    builder.add_builder(box, label_prefix="World")

    pendulum = newton.ModelBuilder()
    link = pendulum.add_link(xform=wp.transform((1, 0, 0), wp.quat_identity()), label="Link")
    pendulum.add_shape_box(link, hx=0.06, hy=0.06, hz=0.35, label="Link/geometry", color=(0.9, 0.45, 0.15))
    joint = pendulum.add_joint_revolute(
        -1,
        link,
        parent_xform=wp.transform((1, 0, 1.5), wp.quat_identity()),
        child_xform=wp.transform((0, 0, 0.35), wp.quat_identity()),
        axis=(0, 1, 0),
        label="Hinge",
    )
    pendulum.joint_q[pendulum.joint_q_start[joint]] = -pi / 4
    pendulum.add_articulation([joint], label="Pendulum")
    builder.add_builder(pendulum, label_prefix="World/Pendulum")
    return builder


def robot_and_tubes_builder():
    from .robot_scene import build_robot_and_tubes

    return build_robot_and_tubes()


def robot_driver_factory(model):
    from .robot_scene import RobotDriver

    return RobotDriver(model)


def usd_lab_builder():
    from .usd_scene import usd_lab_builder as build

    return build()


SCENE_CONFIGS = {
    "falling-boxes": SceneConfig(falling_boxes_builder),
    "robot-and-tubes": SceneConfig(
        robot_and_tubes_builder,
        solver="mujoco",
        driver_factory=robot_driver_factory,
        warmup_steps=120,
    ),
    "usd-lab": SceneConfig(usd_lab_builder),
}


def get_scene_config(scene_id: str) -> SceneConfig:
    try:
        return SCENE_CONFIGS[scene_id]
    except KeyError:
        raise ValueError(f"Unknown registered Newton scene: {scene_id}") from None


def build_scene(scene_id: str):
    return get_scene_config(scene_id).builder()
