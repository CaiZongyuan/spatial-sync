"""Registered, reproducible scene builders. Arbitrary paths are never accepted by the API."""

from collections.abc import Callable
from dataclasses import dataclass
from math import pi
from typing import Any

BOX, FLOOR, PENDULUM = "World/Box", "World/Floor", "World/Pendulum"
PENDULUM_LINK, PENDULUM_JOINT = PENDULUM + "/Link", PENDULUM + "/Hinge"
FALLING_LABELS = {
    "entities": ["/World", "/" + BOX, "/" + FLOOR, "/" + PENDULUM, "/" + PENDULUM_LINK],
    "bodies": ["/" + BOX, "/" + PENDULUM_LINK],
    "sites": ["/" + PENDULUM_JOINT + "/anchor"],
    "joints": {"/" + PENDULUM_JOINT: {"kind": "revolute", "limits": None}},
}


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
    builder.add_ground_plane(label=FLOOR)

    box = newton.ModelBuilder()
    body = box.add_body(xform=wp.transform((0, 0, 1.5), wp.quat_identity()), label=BOX.rsplit("/", 1)[1])
    box.add_shape_box(body, hx=0.15, hy=0.2, hz=0.15, label="Box/geometry", color=(0.2, 0.55, 0.9))
    builder.add_builder(box, label_prefix=BOX.rsplit("/", 1)[0])

    pendulum = newton.ModelBuilder()
    link = pendulum.add_link(
        xform=wp.transform((1, 0, 0), wp.quat_identity()), label=PENDULUM_LINK.rsplit("/", 1)[1]
    )
    pendulum.add_shape_box(link, hx=0.06, hy=0.06, hz=0.35, label="Link/geometry", color=(0.9, 0.45, 0.15))
    joint = pendulum.add_joint_revolute(
        -1,
        link,
        parent_xform=wp.transform((1, 0, 1.5), wp.quat_identity()),
        child_xform=wp.transform((0, 0, 0.35), wp.quat_identity()),
        axis=(0, 1, 0),
        label=PENDULUM_JOINT.rsplit("/", 1)[1],
    )
    pendulum.joint_q[pendulum.joint_q_start[joint]] = -pi / 4
    pendulum.add_articulation([joint], label="Pendulum")
    builder.add_builder(pendulum, label_prefix=PENDULUM)
    return builder


def robot_and_tubes_builder():
    from .robot_scene import build_robot_and_tubes

    return build_robot_and_tubes()


def usd_lab_builder():
    from .usd_scene import usd_lab_builder as build

    return build()


SCENE_CONFIGS = {
    "falling-boxes": SceneConfig(falling_boxes_builder),
    "robot-and-tubes": SceneConfig(
        robot_and_tubes_builder,
        solver="mujoco",
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
