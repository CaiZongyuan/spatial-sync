"""Bounded real-engine proof: uv run --extra simulation --extra usd python tools/smoke_newton.py."""

import argparse
import hashlib
import json
import platform
import sys
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import newton
import numpy as np

from packages.sim_runtime.loaders import import_robot, import_usd
from packages.sim_runtime.simulation import NewtonSimulation


def main():
    parser = argparse.ArgumentParser(description="Run real Newton smoke and print an auditable JSON receipt")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--steps", type=int, default=24)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = perf_counter()
    with NewtonSimulation(device=args.device) as sim:
        initial = sim.body_poses()
        sim.step(args.steps)
        stepped = sim.body_poses()
        assert stepped[0, 2] < initial[0, 2], "Newton box did not fall"
        stepped_time = sim.sim_time
        sim.pause()
        sim.advance(1)
        assert sim.sim_time == stepped_time, "paused simulation advanced"
        sim.step()
        assert sim.sim_time > stepped_time, "paused manual step did not advance"
        sim.reset()
        reset = sim.body_poses()
        np.testing.assert_allclose(reset, initial, atol=1e-6)
        result = {
            "status": "passed",
            "device": str(sim.model.device),
            "solver": type(sim.solver).__name__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "versions": {
                name: version(name)
                for name in ("newton", "warp-lang", "mujoco", "mujoco-warp", "usd-core", "viser")
            },
            "physicsDt": sim.physics_dt,
            "steps": args.steps,
            "simTime": stepped_time,
            "entityKey": "/" + sim.model.body_label[0].lstrip("/"),
            "initialBodyPose": initial[0].tolist(),
            "steppedBodyPose": stepped[0].tolist(),
            "initialPendulumPose": initial[1].tolist(),
            "steppedPendulumPose": stepped[1].tolist(),
            "resetEpoch": sim.epoch,
            "resetMaxError": float(np.max(np.abs(initial - reset))),
            "labelFields": {
                name: getattr(sim.model, name) for name in ("body_label", "shape_label", "joint_label")
            },
            "readback": "one synchronous bulk body_q.numpy per observation; asynchronous path is P0-E",
        }
    builder = newton.ModelBuilder()
    import_robot(builder, ROOT / "examples/falling-boxes/import-smoke.xml", prefix="World/Robot")
    result["mjcfLabels"] = {
        name: getattr(builder, name) for name in ("body_label", "shape_label", "joint_label")
    }
    usd = newton.ModelBuilder()
    paths = import_usd(usd, ROOT / "examples/falling-boxes/import-smoke.usda")
    result["usdPathMaps"] = {
        name: paths[name] for name in ("path_body_map", "path_shape_map", "path_joint_map")
    }
    for kind in ("body", "shape", "joint"):
        for path, index in paths[f"path_{kind}_map"].items():
            assert getattr(usd, f"{kind}_label")[index] == path
    result["sceneHash"] = hashlib.sha256((ROOT / "packages/sim_runtime/scenes.py").read_bytes()).hexdigest()
    result["elapsedMs"] = round((perf_counter() - started) * 1000, 3)
    receipt = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(receipt + "\n", encoding="utf-8")
    print(receipt)


if __name__ == "__main__":
    main()
