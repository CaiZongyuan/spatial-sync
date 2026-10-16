"""Check real articulated work before reporting bounded solver timings.

Thirty-step timings cover the common precontact subset only. The full 0.5 s laboratory
contact journey has a separate correctness result and is never counted as valid work
when tubes escape the workbench or poses become nonfinite.
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
    with NewtonSimulation("robot-and-tubes", device=device, solver=solver) as sim:
        manifest = export_manifest(
            sim, session_id="comparison", scene_revision="comparison", revision_numeric_id=1
        )
        tube_indices = [i for i, label in enumerate(sim.model.body_label) if "/Tubes/Tube" in label]
        initial = sim.body_poses()
        setup_ms = (perf_counter() - setup) * 1000
        sim.step(30)
        wp.synchronize_device(sim.model.device)
        samples = []
        for _ in range(3):
            sim.reset()
            wp.synchronize_device(sim.model.device)
            start = perf_counter()
            sim.step(30)
            wp.synchronize_device(sim.model.device)
            samples.append((perf_counter() - start) * 1000)
            poses = sim.body_poses()
            assert np.isfinite(poses).all(), f"{solver} failed the precontact subset"
            assert np.all(poses[tube_indices, 2] > 0.9)
            assert anchor_error(sim, manifest) < 1e-5
        sim.reset()
        sim.step(120)
        poses = sim.body_poses()
        finite = bool(np.isfinite(poses).all())
        tube_bounds = bool(
            finite
            and np.all(poses[tube_indices, 2] > 0.74)
            and np.all(poses[tube_indices, 2] < initial[tube_indices, 2] + 0.05)
        )
        full_supported = finite and tube_bounds
        if solver == "mujoco":
            assert full_supported, "Default MuJoCo failed the full laboratory contact journey"
        return {
            "solver": type(sim.solver).__name__,
            "device": str(sim.model.device),
            "physicsDt": sim.physics_dt,
            "bodyCount": sim.model.body_count,
            "armDofs": 7,
            "fingerDofs": 2,
            "dynamicTubes": 20,
            "contacts": "native MuJoCo" if solver == "mujoco" else "Newton CollisionPipeline",
            "setupMs": setup_ms,
            "warmupSteps": 30,
            "timingScope": (
                "30 steps / 0.125 s precontact physics; motor/control/contact traversal; "
                "synchronized completion"
            ),
            "samplesMs": samples,
            "medianMs": statistics.median(samples),
            "fullContactJourney": {
                "steps": 120,
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
        "scope": "Local solver support check; precontact timings do not establish full-demo throughput",
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
