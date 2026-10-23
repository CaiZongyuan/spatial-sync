"""Copied into the trainer environment; imports only stdlib until Isaac calls install."""

import hashlib
import importlib.metadata
import json
import os
import statistics
import sys
import time
from pathlib import Path


def install():
    from rsl_rl.runners import OnPolicyRunner

    output = Path(os.environ["LW_NODE_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    inputs = json.loads(Path(os.environ["LW_NODE_INPUTS"]).read_text(encoding="utf-8"))
    job = inputs["job"]
    names = {
        "isaaclab": "isaaclab",
        "newton": "newton",
        "warp": "warp-lang",
        "torch": "torch",
        "rsl_rl": "rsl-rl-lib",
    }
    versions = {
        "versions": {name: importlib.metadata.version(distribution) for name, distribution in names.items()},
        "hardware": inputs["hardware"],
        **{
            name: job[name]
            for name in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
        },
    }
    if versions["versions"]["rsl_rl"] != inputs["plan"]["algorithm"]["version"]:
        raise RuntimeError("Installed RSL-RL version differs from the approved plan")
    declared = inputs.get("task", {})
    presets = declared.get("isaaclab", {}).get("presets", []) if isinstance(declared, dict) else []
    if not presets:
        presets = next(
            (arg.split("=", 1)[1].split(",") for arg in sys.argv if arg.startswith("presets=")), []
        )
    if (
        versions["hardware"]["device"] == "cpu"
        and versions["versions"]["isaaclab"] == "3.0.0"
        and versions["versions"]["newton"] == "1.6.0rc1"
        and "newton_mjwarp" in presets
    ):
        raise RuntimeError(
            "This frozen IsaacLab3/Newton1.6 native CPU runtime lacks contact reporting; "
            "no approved environment count or physics setting was changed"
        )
    (output / "versions.json").write_text(json.dumps(versions), encoding="utf-8")
    original_learn = OnPolicyRunner.learn

    def learn(runner, *args, **kwargs):
        original_log = runner.logger.log
        started = time.monotonic()

        def log(*args, **kwargs):
            result = original_log(*args, **kwargs)
            row = {
                "iteration": int(kwargs.get("it", runner.current_learning_iteration)) + 1,
                "elapsedSeconds": time.monotonic() - started,
                "meanReward": statistics.mean(runner.logger.rewbuffer) if runner.logger.rewbuffer else None,
                "trainSuccess": None,
                "memoryUsedMb": None,
            }
            with (output / "metrics.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, allow_nan=False) + "\n")
            return result

        runner.logger.log = log
        result = original_learn(runner, *args, **kwargs)
        checkpoint = Path(runner.logger.log_dir) / f"model_{runner.current_learning_iteration}.pt"
        relative = checkpoint.resolve().relative_to((Path.cwd() / "logs").resolve())
        with checkpoint.open("rb") as stream:
            checkpoint_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        runner.export_policy_to_onnx(str(output), filename="policy.onnx")
        with (output / "policy.onnx").open("rb") as stream:
            policy_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        (output / "export.json").write_text(
            json.dumps(
                {
                    "checkpoint": "checkpoints/" + relative.as_posix(),
                    "checkpointSha256": checkpoint_hash,
                    "policy": "policy.onnx",
                    "policySha256": policy_hash,
                    "iteration": runner.current_learning_iteration,
                    "afterLearn": True,
                }
            ),
            encoding="utf-8",
        )
        return result

    OnPolicyRunner.learn = learn
    return None
