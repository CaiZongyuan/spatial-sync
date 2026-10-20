"""Read archived T0 evidence on CPU, without importing a physics backend."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import re
import statistics
import sys
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import torch

torch.set_num_threads(1)
ROOT = Path("D:/Projects/Robots/spatial-sync")
OLD = ROOT / ".scratch/rsi-platform/issue-29"
OUT = ROOT / ".scratch/rsi-platform/issue-29-round2"
TUTORIAL = Path("D:/Projects/Robots/IsaacLabTutorial")


def digest(path):
    return hashlib.file_digest(Path(path).open("rb"), "sha256").hexdigest()


def stats(values):
    values = [float(x) for x in values]
    return (
        {
            "n": len(values),
            "min": min(values),
            "median": statistics.median(values),
            "mean": statistics.mean(values),
            "max": max(values),
        }
        if values
        else {"n": 0}
    )


def tensor_tree(value):
    if isinstance(value, torch.Tensor):
        return {
            "shape": list(value.shape),
            "dtype": str(value.dtype),
            "device": str(value.device),
            "finite": bool(torch.isfinite(value).all()),
        }
    if isinstance(value, dict):
        return {str(k): tensor_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [tensor_tree(v) for v in value]
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(type(value))


def event_times(directory):
    from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

    accumulator = EventAccumulator(str(directory), size_guidance={"scalars": 0})
    accumulator.Reload()
    tags = accumulator.Tags()["scalars"]
    result = {"directory": str(directory), "tags": tags, "timing": {}}
    for tag in tags:
        if any(part in tag.lower() for part in ("collection_time", "learning_time", "total_time", "fps")):
            events = accumulator.Scalars(tag)
            result["timing"][tag] = {
                "all": stats(e.value for e in events),
                "after20": stats(e.value for e in events if e.step >= 20),
                "first_step": events[0].step,
                "last_step": events[-1].step,
            }
    collect = {e.step: e.value for e in accumulator.Scalars("Perf/collection_time")}
    learn = {e.step: e.value for e in accumulator.Scalars("Perf/learning_time")}
    result["timing"]["paired_collection_plus_learning_seconds"] = stats(
        collect[step] + learn[step] for step in sorted(collect.keys() & learn.keys())
    )
    config = (directory / "params/env.yaml").read_text(encoding="utf-8")
    result["configured_num_envs"] = [
        int(value) for value in re.findall(r"^\s*num_envs:\s*(\d+)", config, re.M)
    ]
    agent = (directory / "params/agent.yaml").read_text(encoding="utf-8")
    result["configured_steps_per_env"] = int(re.search(r"^num_steps_per_env:\s*(\d+)", agent, re.M)[1])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT / "cpu-evidence-v1.json")
    parser.add_argument("--check-acceptance", action="store_true")
    args = parser.parse_args()
    before = torch.cuda.is_initialized()
    assert not before
    final = json.loads((OLD / "semantic-final-result.json").read_text(encoding="utf-8"))
    gate = final["audit"]["result"]["contact_grasp_successes"] >= 922
    if args.check_acceptance:
        print(
            json.dumps(
                {
                    "archived_oracle_replay": "RED" if not gate else "GREEN",
                    "successes": final["audit"]["result"]["contact_grasp_successes"],
                    "episodes": final["audit"]["result"]["episodes"],
                    "required": 922,
                    "range": 400,
                    "timeout": 624,
                    "cuda_initialized": torch.cuda.is_initialized(),
                }
            )
        )
        return 0 if gate else 1
    checkpoint = Path(final["selected_checkpoint"]["path"])
    dataset = OLD / "semantic-open375-resets-v1.pt"
    model = torch.load(checkpoint, map_location="cpu", weights_only=True)
    resets = torch.load(dataset, map_location="cpu", weights_only=True)
    trace = torch.load(
        OLD / "home-model630-first-failure-20261020-001733761/trace.pt", map_location="cpu", weights_only=True
    )
    states = resets["states"]
    phases, counts = torch.unique(states["phase"], return_counts=True)
    actions = trace["actual_mean_action_history"].double()
    grip = actions[:, :, -1]
    phase_summary = {}
    for phase in phases:
        ids = states["phase"] == phase
        phase_summary[str(int(phase))] = {
            "count": int(ids.sum()),
            "joint_position_min": states["joint_position"][ids].amin(0).tolist(),
            "joint_position_max": states["joint_position"][ids].amax(0).tolist(),
            "tube_xyz_min": states["tube_pose"][ids, :3].amin(0).tolist(),
            "tube_xyz_max": states["tube_pose"][ids, :3].amax(0).tolist(),
        }
    with (OLD / "semantic-training-iterations.csv").open(encoding="utf-8") as stream:
        rows = [r for r in csv.DictReader(stream) if r["phase"] == "semantic-open375-resume768-v5"]
    iteration_metrics = {
        key: stats(float(r[key]) for r in rows)
        for key in (
            "iteration_seconds",
            "collection_seconds",
            "learning_seconds",
            "mean_episode_length",
            "curriculum_episode_success",
            "curriculum_episode_timeout",
            "curriculum_episode_invalid",
        )
    }
    identity = json.loads((OLD / "semantic-open375-inputs-resume-v5.json").read_text(encoding="utf-8"))
    hashes = {
        key: {"path": item["path"], "expected": item["sha256"], "actual": digest(item["path"])}
        for key, item in identity["files"].items()
        if key in {"robot_usd", "neutral_physics", "tube", "rack", "table", "dataset", "runtime_verifier"}
    }
    hashes["checkpoint"] = {
        "path": str(checkpoint),
        "actual": digest(checkpoint),
        "expected": final["selected_checkpoint"]["sha256"],
    }
    output = {
        "source_baseline": "1abcd49084a2491d7654a2a699fc98926e9467dc",
        "python": sys.executable,
        "package_versions": {
            name: importlib.metadata.version(name)
            for name in ("isaaclab", "newton", "warp-lang", "torch", "rsl-rl-lib", "mujoco-warp")
        },
        "hashes": hashes,
        "checkpoint_tree": tensor_tree(model),
        "dataset_tree": tensor_tree(resets),
        "checkpoint_action_std": model["actor_state_dict"]["distribution.log_std_param"].exp().tolist(),
        "phases": phase_summary,
        "actual_mean_actions_prefix": {
            "shape": list(actions.shape),
            "scope": "111 controls, same 1024 worlds; not timeout trajectories",
            "per_action_min": actions.amin((0, 1)).tolist(),
            "per_action_max": actions.amax((0, 1)).tolist(),
            "per_action_mean": actions.mean((0, 1)).tolist(),
            "gripper_quantiles": torch.quantile(
                grip.flatten(), torch.tensor([0.0, 0.1, 0.5, 0.9, 1.0], dtype=torch.float64)
            ).tolist(),
            "gripper_fraction_close_below_minus005": float((grip < -0.05).double().mean()),
            "gripper_fraction_open_above005": float((grip > 0.05).double().mean()),
            "gripper_fraction_abs_below005": float((grip.abs() < 0.05).double().mean()),
            "gripper_mean_first32": float(grip[:32].mean()),
            "gripper_mean_last32": float(grip[-32:].mean()),
            "adjacent_action_mean_abs_delta": (actions[1:] - actions[:-1]).abs().mean((0, 1)).tolist(),
        },
        "old_training": iteration_metrics,
        "tutorial_timing": event_times(TUTORIAL / "logs/rsl_rl/so101_vial_state/2026-09-25_15-42-47_state"),
        "cpu_only": {
            "cuda_initialized_before": before,
            "cuda_initialized_after": torch.cuda.is_initialized(),
            "simulator_modules_imported": [
                name
                for name in sys.modules
                if name.split(".")[0] in {"warp", "newton", "isaaclab", "mujoco", "mujoco_warp"}
            ],
        },
    }
    assert all(item["expected"] == item["actual"] for item in hashes.values())
    assert not output["cpu_only"]["cuda_initialized_after"]
    assert not output["cpu_only"]["simulator_modules_imported"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2, ensure_ascii=False)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "output_sha256": digest(args.output),
                "cpu_only": output["cpu_only"],
                "old_training": iteration_metrics,
                "tutorial_timing": output["tutorial_timing"]["timing"],
                "prefix_actions": output["actual_mean_actions_prefix"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
