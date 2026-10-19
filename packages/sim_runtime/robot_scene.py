"""Offline FR3, reachable taught rack, bottom-origin tubes and physical centrifuge."""

import newton
import warp as wp
from newton.solvers import SolverMuJoCo

from .robot_definition import (
    ASSET,
    FLOOR,
    HOUSING,
    LID,
    LID_JOINT,
    LID_LIMITS,
    RACK_PLATE,
    RACK_POINTS,
    RACK_SITES,
    ROBOT_FLOATING,
    ROBOT_PREFIX,
    ROTOR,
    ROTOR_CENTER,
    SLOT_OFFSETS,
    SLOT_SITES,
    SPINDLE,
    TCP,
    TUBES,
    WORKBENCH_LEGS,
    WORKBENCH_TOP,
    slot_geometry_labels,
)
from .welds import declare_welds

HOME = (0.0, -0.4, 0.0, -2.0, 0.0, 1.6, 0.7)
STIFFNESS = (24000, 24000, 20000, 16000, 10000, 8000, 4000)
DAMPING = (240, 240, 200, 160, 100, 80, 40)


def _transform(position):
    return wp.transform(position, wp.quat_identity())


def _slot(builder, body, bottom, prefix):
    """Narrow support lets the real finger pads extend below the bottom Site."""
    x, y, z = bottom
    support, guides = slot_geometry_labels(prefix)
    builder.add_shape_box(
        body,
        xform=_transform((x, y, z - 0.012)),
        hx=0.007,
        hy=0.007,
        hz=0.012,
        label=support,
        color=(0.3, 0.35, 0.4),
    )
    for side, label in zip((-1, 1), guides, strict=True):
        builder.add_shape_box(
            body,
            xform=_transform((x + side * 0.014, y, z + 0.008)),
            hx=0.002,
            hy=0.012,
            hz=0.014,
            label=label,
            color=(0.5, 0.55, 0.6),
        )


def build_robot_and_tubes():
    builder = newton.ModelBuilder(up_axis=newton.Axis.Z)
    SolverMuJoCo.register_custom_attributes(builder)
    builder.default_shape_cfg.mu = 1.5
    builder.add_ground_plane(label=FLOOR)
    builder.add_shape_box(
        -1,
        xform=_transform((0.3, 0, 0.7)),
        hx=1.6,
        hy=0.8,
        hz=0.05,
        label=WORKBENCH_TOP,
        color=(0.45, 0.5, 0.56),
    )
    for label, (x, y) in WORKBENCH_LEGS:
        builder.add_shape_box(
            -1,
            xform=_transform((x, y, 0.325)),
            hx=0.045,
            hy=0.045,
            hz=0.325,
            label=label,
            color=(0.2, 0.25, 0.3),
        )
    robot = newton.ModelBuilder(up_axis=newton.Axis.Z)
    SolverMuJoCo.register_custom_attributes(robot)
    robot.add_urdf(
        str(ASSET),
        floating=ROBOT_FLOATING,
        enable_self_collisions=False,
        collapse_fixed_joints=False,
        joint_damping_mode="passive",
    )
    imported_shape_count = robot.shape_count
    robot.shape_material_mu[:] = [2.0] * robot.shape_count
    for joint, label in enumerate(robot.joint_label):
        name = label.rsplit("/", 1)[-1]
        coord, dof = robot.joint_q_start[joint], robot.joint_qd_start[joint]
        if name in {f"fr3_joint{i}" for i in range(1, 8)}:
            arm = int(name.removeprefix("fr3_joint")) - 1
            robot.joint_q[coord] = robot.joint_target_q[coord] = HOME[arm]
            robot.joint_target_mode[dof] = newton.JointTargetMode.POSITION
            robot.joint_target_ke[dof], robot.joint_target_kd[dof] = STIFFNESS[arm], DAMPING[arm]
        elif name.startswith("fr3_finger_joint"):
            robot.joint_q[coord] = robot.joint_target_q[coord] = 0.04
            robot.joint_target_mode[dof] = newton.JointTargetMode.POSITION
            robot.joint_target_ke[dof], robot.joint_target_kd[dof] = 2000, 20
    hand = robot.body_label.index("fr3/fr3_hand")
    robot.add_site(hand, xform=_transform((0, 0, 0.1034)), label=TCP, visible=True)
    shape_offset, body_offset = builder.shape_count, builder.body_count
    builder.add_builder(robot, xform=_transform((-0.5, 0, 0.75)), label_prefix=ROBOT_PREFIX)
    builder.spatial_missing_labels = {("shape", shape_offset + i) for i in range(imported_shape_count)}
    hand += body_offset
    builder.add_shape_box(
        -1,
        xform=_transform((-0.0875, -0.19, 0.80)),
        hx=0.13,
        hy=0.105,
        hz=0.012,
        label=RACK_PLATE,
        color=(0.25, 0.5, 0.65),
    )
    for (_, point), site in zip(RACK_POINTS, RACK_SITES, strict=True):
        _slot(builder, -1, point, site + "Geometry")
        builder.add_site(-1, xform=_transform(point), label=site, visible=True)
    tubes = []
    for index, prefix in enumerate(TUBES):
        point = (
            RACK_POINTS[index][1]
            if index < 4
            else (0.12 + (index % 5) * 0.13, -0.42 + (index // 5) * 0.16, 1.03 + (index // 5) * 0.035)
        )
        rotation = (
            wp.quat_identity() if index < 4 else wp.quat_from_axis_angle(wp.vec3(0, 1, 0), 0.03 * (index % 3))
        )
        if index >= 4:
            offset = wp.quat_rotate(rotation, wp.vec3(0, 0, 0.071))
            point = tuple(point[axis] - offset[axis] for axis in range(3))
        tube = builder.add_body(xform=wp.transform(point, rotation), label=prefix)
        if index < 4:
            # A30mm microtube fits below the palm at a bottom-Site grasp.
            builder.add_shape_cylinder(
                tube,
                xform=_transform((0, 0, 0.015)),
                radius=0.008,
                half_height=0.015,
                label=prefix + "/Glass",
                color=(0.15, 0.7, 0.85),
            )
            builder.add_shape_box(
                tube,
                xform=_transform((0, 0, 0.002)),
                hx=0.005,
                hy=0.005,
                hz=0.002,
                label=prefix + "/FlatBase",
                color=(0.12, 0.6, 0.75),
            )
        else:
            # Retain legacy world geometry while making every body bottom-origin.
            builder.add_shape_capsule(
                tube,
                xform=_transform((0, 0, 0.071)),
                radius=0.016,
                half_height=0.055,
                label=prefix + "/Glass",
                color=(0.15, 0.7, 0.85),
            )
        tubes.append(tube)
    builder.add_shape_box(
        -1,
        xform=_transform((0.06, 0.20, 0.785)),
        hx=0.175,
        hy=0.115,
        hz=0.035,
        label=HOUSING,
        color=(0.75, 0.8, 0.86),
    )
    rotor = builder.add_link(xform=_transform(ROTOR_CENTER), label=ROTOR)
    builder.add_shape_cylinder(
        rotor,
        radius=0.075,
        half_height=0.004,
        label="World/Lab/Centrifuge/Rotor/Geometry",
        color=(0.95, 0.45, 0.12),
    )
    spindle = builder.add_joint_revolute(
        -1,
        rotor,
        parent_xform=_transform(ROTOR_CENTER),
        axis=(0, 0, 1),
        target_ke=0,
        target_kd=5,
        actuator_mode=newton.JointTargetMode.VELOCITY,
        label=SPINDLE,
    )
    joints, slots = [spindle], []
    for prefix, offset in zip(SLOT_SITES, SLOT_OFFSETS, strict=True):
        body = builder.add_link(
            xform=_transform(tuple(a + b for a, b in zip(ROTOR_CENTER, offset))), label=prefix + "Body"
        )
        joints.append(
            builder.add_joint_fixed(rotor, body, parent_xform=_transform(offset), label=prefix + "Mount")
        )
        _slot(builder, body, (0, 0, 0), prefix + "Body")
        builder.add_site(body, label=prefix, visible=True)
        slots.append(body)
    builder.add_articulation(joints, label="World/Lab/Centrifuge/RotorAssembly")
    # Hinge on the far side keeps the raised physical lid out of the taught
    # arm corridor. The prior rear hinge intersected link5 and the palm.
    lid_origin = (0.235, 0.20, 0.985)
    lid = builder.add_link(xform=_transform(lid_origin), label=LID)
    builder.add_shape_box(
        lid,
        xform=_transform((-0.175, 0, 0)),
        hx=0.175,
        hy=0.115,
        hz=0.006,
        label="World/Lab/Centrifuge/Lid/Geometry",
        color=(0.4, 0.6, 0.7),
    )
    hinge = builder.add_joint_revolute(
        -1,
        lid,
        parent_xform=_transform(lid_origin),
        axis=(0, 1, 0),
        limit_lower=LID_LIMITS[0],
        limit_upper=LID_LIMITS[1],
        target_ke=300,
        target_kd=20,
        actuator_mode=newton.JointTargetMode.POSITION,
        label=LID_JOINT,
    )
    builder.joint_q[builder.joint_q_start[hinge]] = 1.4
    builder.joint_target_q[builder.joint_q_start[hinge]] = 1.4
    builder.add_articulation([hinge], label="World/Lab/Centrifuge/LidAssembly")
    declare_welds(builder, [hand, *slots], tubes)
    return builder
