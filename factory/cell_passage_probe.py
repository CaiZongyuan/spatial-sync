"""Minimal real FR3/carrier risk probe; no bodies are moved after initialization."""

import argparse
import json
import re
from pathlib import Path

import newton
import numpy as np
import warp as wp
from newton.solvers import SolverMuJoCo
from pxr import Usd

from factory.cell_passage import HANDLE, box, plate, translate
from packages.programs.labware.grasp import grasp_geometry
from packages.programs.robot_arm.trajectory import plan_trajectory
from packages.sim_runtime.loaders import import_usd
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation
from packages.sim_runtime.welds import declare_weld_pairs

ROBOT = "/World/Lab/Robot"
BODY = "/World/Lab/Labware/Mother"


def probe(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    text = (source / "scene/stage.usda").read_text()
    text = re.sub(
        r"library:assets/[^/]+/([^/]+)/1/asset.usda",
        lambda m: (source / "assets" / m[1] / "asset.usda").as_posix(),
        text,
    )
    stage_file = output / "probe.usda"
    stage_file.write_text(text)
    stage = Usd.Stage.Open(str(stage_file))
    # This is a minimal qualification fixture, deliberately isolated from the
    # production scene. Retain exactly the original robot, rail and collisions.
    for prim in list(stage.GetPrimAtPath("/World/Lab").GetChildren()):
        if prim.GetName() not in ("Robot", "RailBase", "RailCarriage", "Joints"):
            stage.RemovePrim(prim.GetPath())
    for prim in list(stage.GetPrimAtPath("/World/Lab/Joints").GetChildren()):
        if prim.GetName() not in ("RailFixed", "RailCarriage"):
            stage.RemovePrim(prim.GetPath())
    stage.RemovePrim("/World/Dressing")

    def basic_builder():
        result = newton.ModelBuilder(up_axis=newton.Axis.Z)
        SolverMuJoCo.register_custom_attributes(result)
        paths = import_usd(result, stage, root_path="/World/Lab", hide_collision_shapes=False)
        hand = next(
            int(index) for path, index in paths["path_body_map"].items() if str(path) == ROBOT + "/fr3_hand"
        )
        result.add_site(
            hand,
            xform=wp.transform((0, 0, 0.1034), wp.quat_identity()),
            label=ROBOT + "/fr3_hand/PhysicalTCP",
        )
        return result

    # Independent FK derives two reachable support positions from bounded joint
    # configurations, without changing the physical TCP or link dimensions.
    with wp.ScopedDevice("cpu"):
        first = basic_builder()
    model = first.finalize(device="cpu")
    state = model.state()
    hand = model.body_label.index(ROBOT + "/fr3_hand")
    joints = [model.joint_label.index(ROBOT + f"/Joints/fr3_joint{i}") for i in range(1, 8)]
    indices = model.joint_q_start.numpy()[joints]
    q = model.joint_q.numpy().copy()
    home = q[indices].copy()
    grasp = np.array([0, -0.2, 0, -1.8, 0, 1.6, 0.785398])
    destination = grasp.copy()
    destination[0] += 0.45

    def tcp_at(values):
        q[indices] = values
        state.joint_q.assign(q)
        newton.eval_fk(model, state.joint_q, state.joint_qd, state)
        pose = state.body_q.numpy()[hand]
        return np.asarray(
            wp.transform_point(wp.transform(tuple(pose[:3]), tuple(pose[3:])), wp.vec3(0, 0, 0.1034))
        )

    source_point, dest_point = tcp_at(grasp), tcp_at(destination)
    yaw = destination[0] - grasp[0]
    rotated_handle = np.array([HANDLE[0] * np.cos(yaw), HANDLE[0] * np.sin(yaw), HANDLE[2]])
    origins = [source_point - np.asarray(HANDLE), dest_point - rotated_handle]
    carrier = plate(stage, BODY)
    translate(carrier, origins[0])
    for index, point in enumerate(origins):
        support = box(
            stage, f"/World/Lab/Support{index}", (point[0], point[1], point[2] - 0.01), (0.135, 0.092, 0.02)
        )
        support.AddRotateZOp().Set(float(np.degrees(yaw)) if index else 0)
    stage.GetRootLayer().Save()

    def builder():
        result = basic_builder()
        declare_weld_pairs(
            result, [(result.body_label.index(ROBOT + "/fr3_hand"), result.body_label.index(BODY))]
        )
        return result

    sim = NewtonSimulation("carrier-probe", device="cpu", config=SceneConfig(builder, solver="mujoco"))
    manifest = export_manifest(sim, session_id="passage-probe", scene_revision="probe", revision_numeric_id=1)
    port = PhysicsPort(sim, manifest)
    arm = [port.bind_joint(ROBOT + f"/Joints/fr3_joint{i}") for i in range(1, 8)]
    fingers = [port.bind_joint(ROBOT + f"/Joints/fr3_finger_joint{i}") for i in (1, 2)]
    hand, body = port.bind_body(ROBOT + "/fr3_hand"), port.bind_body(BODY)
    tcp = port.bind_site(ROBOT + "/fr3_hand/PhysicalTCP")
    samples, contacts, motions = [], [], []

    def advance():
        port.commit()
        sim.step(4)
        port.capture()
        samples.append(
            {
                "time": sim.sim_time,
                "pose": port.body[body].tolist(),
                "opening": sum(float(port.joint_q(f)[0]) for f in fingers),
            }
        )
        mj = sim.solver.mj_model
        for contact in sim.solver.mj_data.contact[: sim.solver.mj_data.ncon]:
            names = [mj.body(int(mj.geom_bodyid[g])).name for g in contact.geom]
            if contact.dist < -0.001 and any("Robot/" in n for n in names):
                contacts.append({"time": sim.sim_time, "bodies": names, "penetrationM": -float(contact.dist)})

    def move(target):
        plan = plan_trajectory(
            [port.joint_q(j)[0] for j in arm], target, [2.62] * 7, speed_scale=0.3, speed=0.5
        )
        for frame in range(round((plan.duration + 2) * 60)):
            values, _ = plan.sample(frame / 60)
            for joint, value in zip(arm, values, strict=True):
                port.set_joint_target(joint, [value])
            advance()
        error = max(abs(float(port.joint_q(j)[0]) - v) for j, v in zip(arm, target, strict=True))
        motions.append({"errorRad": error, "target": np.asarray(target).tolist()})
        if error > 0.01:
            raise RuntimeError(f"Motion did not settle: {error}")

    def gripper(width, capture=False):
        initial = sum(float(port.joint_q(f)[0]) for f in fingers)
        for frame in range(60):
            commanded = initial + np.clip(width - initial, -0.3 * frame / 60, 0.3 * frame / 60)
            for finger in fingers:
                port.set_joint_target(finger, [commanded / 2])
            advance()
            opening = sum(float(port.joint_q(f)[0]) for f in fingers)
            if capture and grasp_geometry(
                port.body[body], np.r_[*port.site_pose(tcp)], HANDLE, 0.017, opening
            ):
                return

    receipt = {
        "sourceTcpM": source_point.tolist(),
        "destinationTcpM": dest_point.tolist(),
        "home": home.tolist(),
    }
    try:
        for _ in range(60):
            advance()
        move(grasp)
        gripper(0, capture=True)
        opening = sum(float(port.joint_q(f)[0]) for f in fingers)
        tcp_pose = np.r_[*port.site_pose(tcp)]
        qualified = grasp_geometry(port.body[body], tcp_pose, HANDLE, 0.017, opening)
        receipt.update(
            graspQualified=qualified,
            actualOpeningM=opening,
            graspBodyPose=port.body[body].tolist(),
            actualTcpPose=tcp_pose.tolist(),
        )
        if not qualified:
            raise RuntimeError("Actual finger/handle geometry failed before weld")
        before = port.body[body].copy()
        port.weld(hand, body)
        advance()
        receipt["activationDisplacementM"] = float(np.linalg.norm(port.body[body, :3] - before[:3]))
        lift = grasp.copy()
        lift[1] -= 0.18
        move(lift)
        lift_dest = destination.copy()
        lift_dest[1] -= 0.18
        move(lift_dest)
        move(destination)
        gripper(0.08)
        port.unweld(hand, body)
        advance()
        move(lift_dest)
        for _ in range(60 * 10):
            advance()
        receipt["finalPose"] = port.body[body].tolist()
        receipt["destinationErrorM"] = float(np.linalg.norm(port.body[body, :3] - origins[1]))
        receipt["success"] = receipt["destinationErrorM"] < 0.005 and not contacts
    except Exception as error:
        receipt["error"] = str(error)
        receipt["success"] = False
    finally:
        receipt.update(motions=motions, collisionCount=len(contacts), collisions=contacts[:20])
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        (output / "samples.json").write_text(json.dumps(samples) + "\n")
        sim.close()
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    probe(args.source, args.out)
