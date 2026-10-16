"""Manual CPU contact20 and runtime-weld acceptance spike; writes measured evidence."""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.programs.robot_arm.trajectory import plan_trajectory
from packages.programs.tube.facts import tube_location
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.simulation import NewtonSimulation


def run(trials, output):
    started = time.monotonic()
    sim = NewtonSimulation("robot-and-tubes", device="cpu")
    manifest = export_manifest(sim, session_id="spike", scene_revision="spike", revision_numeric_id=1)
    port = PhysicsPort(sim, manifest)
    joints = [port.bind_joint(f"/World/Lab/RobotA/fr3/fr3_joint{i}") for i in range(1, 8)]
    fingers = [port.bind_joint(f"/World/Lab/RobotA/fr3/fr3_finger_joint{i}") for i in (1, 2)]
    hand = port.bind_body("/World/Lab/RobotA/fr3/fr3_hand")
    tube = port.bind_body("/World/Lab/Tubes/Tube01")
    tcp = port.bind_site("/World/Lab/RobotA/fr3/fr3_hand/tcp")
    locations = yaml.safe_load((ROOT / "devices/scenes/robot-and-tubes.locations.yaml").read_text())[
        "locations"
    ]
    velocity_limits = [2.62, 2.62, 2.62, 2.62, 5.26, 4.18, 5.26]

    def advance(seconds):
        for _ in range(round(seconds * 60)):
            port.commit()
            sim.step(4)
            port.capture()

    def move(target, speed):
        initial = [port.joint_q(j)[0] for j in joints]
        motion = plan_trajectory(initial, target, velocity_limits, speed_scale=0.5, speed=speed)
        elapsed = 0
        while elapsed <= 2 * motion.duration + 1:
            q, _ = motion.sample(elapsed)
            for j, value in zip(joints, q):
                port.set_joint_target(j, [value])
            advance(1 / 60)
            elapsed += 1 / 60
            error = max(abs(port.joint_q(j)[0] - value) for j, value in zip(joints, target))
            speed_actual = max(abs(port.joint_qd(j)[0]) for j in joints)
            if elapsed >= motion.duration and error < 0.01 and speed_actual < 0.05:
                return {"ok": True, "errorRad": float(error), "seconds": elapsed}
        contact_pairs = []
        model, data = sim.solver.mj_model, sim.solver.mj_data
        for contact in data.contact[: data.ncon]:
            names = [model.body(int(model.geom_bodyid[g])).name for g in contact.geom]
            if contact.dist < 0 and any("RobotA" in name for name in names):
                contact_pairs.append({"bodies": names, "distanceM": float(contact.dist)})
        return {
            "ok": False,
            "errorRad": float(error),
            "speedRadS": float(speed_actual),
            "seconds": elapsed,
            "errorsRad": [float(port.joint_q(j)[0] - value) for j, value in zip(joints, target)],
            "contacts": contact_pairs,
        }

    def gripper(width):
        target = sum(float(port.target_q[finger.target][0]) for finger in fingers)
        for _ in range(30):
            target += float(np.clip(width - target, -0.4 / 60, 0.4 / 60))
            for finger in fingers:
                port.set_joint_target(finger, [target / 2])
            advance(1 / 60)

    # Runtime activation must preserve the current relative pose and release
    # physical authority back to gravity. These bodies are never repositioned.
    sim.step(120)
    port.capture()
    before = port.body[tube].copy()
    port.weld(hand, tube)
    port.commit()
    advance(0.1)
    jump = float(np.linalg.norm(port.body[tube, :3] - before[:3]))
    relative_before = port.local_position(hand, port.body[tube, :3])
    target = np.array([port.joint_q(j)[0] for j in joints])
    target[0] += 0.1
    movement = move(target, 0.5)
    drift = float(np.linalg.norm(port.local_position(hand, port.body[tube, :3]) - relative_before))
    port.unweld(hand, tube)
    port.commit()
    release_pose = port.body[tube, :3].copy()
    advance(0.5)
    released_motion = float(np.linalg.norm(port.body[tube, :3] - release_pose))
    evidence = {
        "weld": {
            "activationDisplacementM": jump,
            "relativeDriftM": drift,
            "releasedDisplacementM": released_motion,
            "armMotion": movement,
        },
        "contactTrials": [],
    }
    for trial in range(trials):
        sim.reset()
        port.reset()
        advance(0.5)
        source, destination = locations["rack.A1"], locations["centrifuge.slot1"]
        bottom_start = port.body[tube, 2]
        initial_pose = port.body[tube].tolist()
        speed = [0.5, 0.6, 0.7, 0.8][trial % 4]
        segments = [move(source["approach"], speed)]
        gripper(0.08)
        segments.append(move(source["grasp"], speed))
        grasp_pose = port.body[tube].tolist()
        tcp_error = float(np.linalg.norm(port.site_pose(tcp)[0] - port.body[tube, :3]))
        gripper(0)
        segments.append(move(source["approach"], speed))
        lift = float(port.body[tube, 2] - bottom_start)
        segments.extend([move(destination["approach"], speed), move(destination["grasp"], speed)])
        gripper(0.08)
        segments.append(move(destination["approach"], speed))
        advance(0.5)
        site = np.r_[*port.site_pose(destination["site"])]
        location = tube_location(port.body[tube], port.velocity[tube], [site], [destination["site"]])
        result = {
            "trial": trial + 1,
            "speed": speed,
            "success": location == destination["site"] and lift >= 0.02,
            "initialPose": initial_pose,
            "graspPose": grasp_pose,
            "liftM": lift,
            "graspTcpErrorM": tcp_error,
            "location": location,
            "tubePose": port.body[tube].tolist(),
            "segments": segments,
        }
        evidence["contactTrials"].append(result)
        print(json.dumps(result), flush=True)
        output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    evidence["successRate"] = (
        sum(t["success"] for t in evidence["contactTrials"]) / trials if trials else None
    )
    evidence["wallSeconds"] = time.monotonic() - started
    output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    sim.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--output", type=Path, default=ROOT / ".scratch/info-i3-newton/spike.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run(args.trials, args.output)
