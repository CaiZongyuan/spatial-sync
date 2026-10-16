"""Offline official Franka FR3 plus physical laboratory objects and a bounded motor driver."""

from pathlib import Path

import newton
import numpy as np
import warp as wp
from newton.solvers import SolverMuJoCo

ROBOT_PREFIX = "World/Lab/RobotA"
HOME = (0.0, -0.4, 0.0, -2.0, 0.0, 1.6, 0.7)
STIFFNESS = (300, 300, 250, 200, 50, 25, 4)
DAMPING = (20, 20, 15, 10, 1, 0.4, 0.05)
ASSET = Path(__file__).resolve().parents[2] / "assets/robots/franka_emika_panda/urdf/fr3_franka_hand.urdf"


def build_robot_and_tubes():
    builder = newton.ModelBuilder(up_axis=newton.Axis.Z)
    SolverMuJoCo.register_custom_attributes(builder)
    builder.add_ground_plane(label="World/Lab/Floor")
    builder.add_shape_box(
        -1,
        xform=wp.transform((0.3, 0, 0.7), wp.quat_identity()),
        hx=1.6,
        hy=0.8,
        hz=0.05,
        label="World/Lab/Workbench/Top",
        color=(0.45, 0.5, 0.56),
    )
    for index, (x, y) in enumerate(((-1.1, -0.65), (-1.1, 0.65), (1.7, -0.65), (1.7, 0.65))):
        builder.add_shape_box(
            -1,
            xform=wp.transform((x, y, 0.325), wp.quat_identity()),
            hx=0.045,
            hy=0.045,
            hz=0.325,
            label=f"World/Lab/Workbench/Leg{index + 1}",
            color=(0.2, 0.25, 0.3),
        )

    robot = newton.ModelBuilder(up_axis=newton.Axis.Z)
    SolverMuJoCo.register_custom_attributes(robot)
    robot.add_urdf(
        str(ASSET),
        floating=False,
        enable_self_collisions=False,
        collapse_fixed_joints=False,
        joint_damping_mode="passive",
    )
    # Newton 1.7's URDF parser omits label= for every imported shape, including
    # named URDF visuals. Preserve that known omission independently of spelling.
    imported_shape_count = robot.shape_count
    for joint, label in enumerate(robot.joint_label):
        name = label.rsplit("/", 1)[-1]
        if name in {f"fr3_joint{i}" for i in range(1, 8)}:
            arm = int(name.removeprefix("fr3_joint")) - 1
            coord, dof = robot.joint_q_start[joint], robot.joint_qd_start[joint]
            robot.joint_q[coord] = HOME[arm]
            robot.joint_target_q[coord] = HOME[arm]
            robot.joint_target_mode[dof] = newton.JointTargetMode.POSITION
            robot.joint_target_ke[dof] = STIFFNESS[arm]
            robot.joint_target_kd[dof] = DAMPING[arm]
        elif name.startswith("fr3_finger_joint"):
            coord, dof = robot.joint_q_start[joint], robot.joint_qd_start[joint]
            robot.joint_q[coord] = robot.joint_target_q[coord] = 0.02
            robot.joint_target_mode[dof] = newton.JointTargetMode.POSITION
            robot.joint_target_ke[dof], robot.joint_target_kd[dof] = 100, 1
    hand = robot.body_label.index("fr3/fr3_hand")
    robot.add_site(
        hand, xform=wp.transform((0, 0, 0.1034), wp.quat_identity()), label="fr3/fr3_hand/tcp", visible=True
    )
    shape_offset = builder.shape_count
    builder.add_builder(
        robot, xform=wp.transform((-0.5, 0, 0.75), wp.quat_identity()), label_prefix=ROBOT_PREFIX
    )
    builder.spatial_missing_labels = {
        ("shape", shape_offset + index) for index in range(imported_shape_count)
    }

    for index in range(20):
        x, y = 0.12 + (index % 5) * 0.13, -0.42 + (index // 5) * 0.16
        tube = builder.add_body(
            xform=wp.transform(
                (x, y, 1.03 + (index // 5) * 0.035),
                wp.quat_from_axis_angle(wp.vec3(0, 1, 0), 0.03 * (index % 3)),
            ),
            label=f"World/Lab/Tubes/Tube{index + 1:02d}",
        )
        builder.add_shape_capsule(
            tube,
            radius=0.016,
            half_height=0.055,
            label=f"World/Lab/Tubes/Tube{index + 1:02d}/Glass",
            color=(0.15, 0.7, 0.85),
        )

    builder.add_shape_box(
        -1,
        xform=wp.transform((1.13, 0.3, 0.91), wp.quat_identity()),
        hx=0.25,
        hy=0.23,
        hz=0.16,
        label="World/Lab/Centrifuge/Housing",
        color=(0.75, 0.8, 0.86),
    )
    rotor = builder.add_link(
        xform=wp.transform((1.13, 0.3, 1.1), wp.quat_identity()), label="World/Lab/Centrifuge/Rotor"
    )
    builder.add_shape_box(
        rotor,
        hx=0.18,
        hy=0.035,
        hz=0.015,
        label="World/Lab/Centrifuge/Rotor/Geometry",
        color=(0.95, 0.45, 0.12),
    )
    joint = builder.add_joint_revolute(
        -1,
        rotor,
        parent_xform=wp.transform((1.13, 0.3, 1.1), wp.quat_identity()),
        axis=(0, 0, 1),
        target_ke=2,
        target_kd=0.05,
        actuator_mode=newton.JointTargetMode.POSITION,
        label="World/Lab/Centrifuge/Spindle",
    )
    builder.add_articulation([joint], label="World/Lab/Centrifuge")
    return builder


@wp.kernel
def _drive_targets(
    home: wp.array[float],
    amplitude: wp.array[float],
    speed: wp.array[float],
    time_value: float,
    targets: wp.array[float],
):
    index = wp.tid()
    targets[index] = home[index] + amplitude[index] * wp.sin(0.7 * time_value) + speed[index] * time_value


class RobotDriver:
    def __init__(self, model):
        targets = model.joint_target_q.numpy()
        amplitude, speed = np.zeros_like(targets), np.zeros_like(targets)
        starts = model.joint_q_start.numpy()
        for joint, label in enumerate(model.joint_label):
            name = label.rsplit("/", 1)[-1]
            if name == "fr3_joint1":
                amplitude[starts[joint]] = 0.14
            elif name == "fr3_joint3":
                amplitude[starts[joint]] = 0.08
            elif name == "fr3_joint7":
                amplitude[starts[joint]] = 0.1
            elif name == "Spindle":
                speed[starts[joint]] = 0.6
        self.home = wp.array(targets, dtype=float, device=model.device)
        self.amplitude = wp.array(amplitude, dtype=float, device=model.device)
        self.speed = wp.array(speed, dtype=float, device=model.device)
        self.device = model.device

    def apply(self, control, sim_time):
        wp.launch(
            _drive_targets,
            dim=self.home.size,
            inputs=[self.home, self.amplitude, self.speed, sim_time],
            outputs=[control.joint_target_q],
            device=self.device,
        )
