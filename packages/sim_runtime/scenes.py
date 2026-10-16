"""Registered, reproducible scene builders. Arbitrary paths are never accepted by the API."""

from math import pi


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


SCENE_BUILDERS = {"falling-boxes": falling_boxes_builder}


def build_scene(scene_id: str):
    try:
        build = SCENE_BUILDERS[scene_id]
    except KeyError:
        raise ValueError(f"Unknown registered Newton scene: {scene_id}") from None
    return build()
