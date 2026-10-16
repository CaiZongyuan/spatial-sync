"""Report solver support before timing valid articulated laboratory work.

The scene contains rack contacts from its initial state. Unsupported reference
runs produce a diagnostic with no timing samples or median.
"""

import argparse
import hashlib
import json
import platform
import statistics
import sys
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import warp as wp

from packages.protocol.math import compose_pose, joint_position_error
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.simulation import NewtonSimulation


def anchor_error(sim, manifest):
    poses = sim.body_poses()
    bodies = {
        "/" + label.lstrip("/"): {"p": row[:3], "q": row[3:]}
        for label, row in zip(sim.model.body_label, poses)
    }
    sites = {site.entityKey: site for site in manifest.sites}
    errors = []
    for joint in manifest.joints:
        if joint.type == "free":
            continue
        parent = joint.parentLocalPose.model_dump()
        if joint.parentBodyKey:
            parent = compose_pose(bodies[joint.parentBodyKey], parent)
        child = compose_pose(bodies[joint.childBodyKey], sites[joint.anchorSiteKey].localPose.model_dump())
        error = joint_position_error(joint.type, parent, child, joint.axis)
        if error is not None:
            errors.append(error)
    return max(errors, default=0)


def check(solver, device):
    setup = perf_counter()
    reference_dt, reference_steps = 1 / 24000, 1200
    with NewtonSimulation("robot-and-tubes", device=device, solver=solver, physics_dt=reference_dt) as sim:
        manifest = export_manifest(
            sim, session_id="comparison", scene_revision="comparison", revision_numeric_id=1
        )
        tube_indices = [i for i, label in enumerate(sim.model.body_label) if "/Tubes/Tube" in label]
        joint = next(i for i, label in enumerate(sim.model.joint_label) if label.endswith("/fr3_joint1"))
        target_start = int(sim.model.joint_target_q_start.numpy()[joint])
        coordinate_start = int(sim.model.joint_q_start.numpy()[joint])
        initial = sim.body_poses()

        def result_at_boundary():
            poses = sim.body_poses()
            coordinates, velocities = sim.state.joint_q.numpy(), sim.state.joint_qd.numpy()
            finite = bool(
                np.isfinite(poses).all() and np.isfinite(coordinates).all() and np.isfinite(velocities).all()
            )
            seated = bool(finite and np.all(np.abs(poses[tube_indices[:4], 2] - 0.84) < 0.02))
            bounded = bool(
                finite
                and np.all(poses[tube_indices, 2] > 0.74)
                and np.all(poses[tube_indices, 2] < initial[tube_indices, 2] + 0.05)
            )
            moved = bool(finite and coordinates[coordinate_start] > 0.005)
            error = anchor_error(sim, manifest) if finite else None
            supported = bool(finite and seated and bounded and moved and error < 1e-5)
            return {
                "supported": supported,
                "finite": finite,
                "loadedTubesSeated": seated,
                "tubeBounds": bounded,
                "armMoved": moved,
                "maxJointPositionError": error,
            }

        def drive():
            targets = sim.control.joint_target_q.numpy().copy()
            targets[target_start] = 0.05
            sim.control.joint_target_q.assign(targets)

        setup_ms = (perf_counter() - setup) * 1000
        drive()
        sim.step(reference_steps)
        wp.synchronize_device(sim.model.device)
        samples = []
        reference = result_at_boundary()
        if reference["supported"]:
            for _ in range(3):
                sim.reset()
                drive()
                wp.synchronize_device(sim.model.device)
                start = perf_counter()
                sim.step(reference_steps)
                wp.synchronize_device(sim.model.device)
                elapsed = (perf_counter() - start) * 1000
                reference = result_at_boundary()
                if not reference["supported"]:
                    samples.clear()
                    break
                samples.append(elapsed)
        reference["diagnostic"] = (
            None
            if reference["supported"]
            else "Unsupported full laboratory reference: invalid articulated/contact work; no timing counted"
        )
        if solver == "mujoco":
            assert reference["supported"], reference
        solver_name, body_count = type(sim.solver).__name__, sim.model.body_count
    # Check the actual 240Hz scene independently of the fine-step reference.
    with NewtonSimulation("robot-and-tubes", device=device, solver=solver) as sim:
        manifest = export_manifest(sim, session_id="support", scene_revision="support", revision_numeric_id=1)
        initial = sim.body_poses()
        sim.step(120)
        poses = sim.body_poses()
        finite = bool(
            np.isfinite(poses).all()
            and np.isfinite(sim.state.joint_q.numpy()).all()
            and np.isfinite(sim.state.joint_qd.numpy()).all()
        )
        tube_bounds = bool(
            finite
            and np.all(poses[tube_indices, 2] > 0.74)
            and np.all(poses[tube_indices, 2] < initial[tube_indices, 2] + 0.05)
        )
        full_supported = finite and tube_bounds
        if solver == "mujoco":
            assert full_supported, "Default MuJoCo failed the full laboratory contact journey"
        return {
            "solver": solver_name,
            "device": str(sim.model.device),
            "physicsDt": reference_dt,
            "bodyCount": body_count,
            "armDofs": 7,
            "fingerDofs": 2,
            "dynamicTubes": 20,
            "contacts": "native MuJoCo" if solver == "mujoco" else "Newton CollisionPipeline",
            "setupMs": setup_ms,
            "warmupSteps": reference_steps,
            "reference": reference,
            "timingScope": (
                "1200 steps / 0.05s at 1/24000s; same actuated joint target and scene; "
                "rack contacts and airborne loose tubes; synchronized completion"
            ),
            "samplesMs": samples,
            "medianMs": statistics.median(samples) if samples else None,
            "fullContactJourney": {
                "steps": 120,
                "physicsDt": sim.physics_dt,
                "simTime": sim.sim_time,
                "finite": finite,
                "supportedAtDefaultDt": full_supported,
                "tubeMinZ": float(poses[tube_indices, 2].min()) if finite else None,
                "tubeMaxZ": float(poses[tube_indices, 2].max()) if finite else None,
                "maxJointPositionError": anchor_error(sim, manifest) if finite else None,
            },
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = {
        "scope": "Local solver support check; no comparison exists when a reference is unsupported",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "versions": {name: version(name) for name in ("newton", "warp-lang", "mujoco", "mujoco-warp")},
        "sceneHash": hashlib.sha256((root / "packages/sim_runtime/robot_scene.py").read_bytes()).hexdigest(),
        "assetManifestHash": hashlib.sha256(
            (root / "assets/robots/franka_emika_panda/provenance.json").read_bytes()
        ).hexdigest(),
        "results": [check(solver, args.device) for solver in ("mujoco", "featherstone")],
    }
    rendered = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
