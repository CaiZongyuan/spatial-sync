"""CPU-only subprocess contract fixture. Its output files are explicitly fake models."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--task")
    parser.add_argument("--device")
    args, unknown = parser.parse_known_args()
    assert args.device == "cpu" and os.environ["CUDA_VISIBLE_DEVICES"] == "-1"
    assert "agent.device=cpu" in unknown and "env.sim.physics.solver_cfg.use_mujoco_cpu=true" in unknown
    assert os.environ["PXR_WORK_THREAD_LIMIT"] == "1" and "SPATIAL_NODE_TOKEN" not in os.environ
    inputs = json.loads(Path(os.environ["LW_NODE_INPUTS"]).read_text(encoding="utf-8"))
    assert args.task == inputs["task"]["isaaclab"]["task_id"]
    registered = importlib.metadata.entry_points(group="isaaclab.tasks", name="probe")
    assert len(registered) == 1 and next(iter(registered)).load()() == "installed-source-marker"
    output = Path(os.environ["LW_NODE_OUTPUT"])
    with (output / "actual-starts.txt").open("a", encoding="utf-8") as stream:
        stream.write("one CPU fixture start\n")
    print("Fake trainer: source installed and pinned packages received", flush=True)
    (output / "metrics.jsonl").write_text(
        json.dumps({"iteration": 1, "meanReward": 2.0}) + "\n", encoding="utf-8"
    )
    versions = {
        "versions": {key: "fixture-only" for key in ("isaaclab", "newton", "warp", "torch", "rsl_rl")},
        "hardware": inputs["hardware"],
        **{
            key: inputs["job"][key]
            for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
        },
    }
    (output / "versions.json").write_text(json.dumps(versions), encoding="utf-8")
    (output / "policy.onnx").write_bytes(b"fake ONNX contract payload")
    logs = Path.cwd() / "logs"
    logs.mkdir()
    (logs / "model_0.pt").write_bytes(b"fake checkpoint contract payload")
    (output / "export.json").write_text(
        json.dumps(
            {
                "checkpoint": "checkpoints/model_0.pt",
                "checkpointSha256": hashlib.sha256((logs / "model_0.pt").read_bytes()).hexdigest(),
                "policy": "policy.onnx",
                "policySha256": hashlib.sha256((output / "policy.onnx").read_bytes()).hexdigest(),
                "iteration": 0,
                "afterLearn": True,
                "externalData": [],
            }
        ),
        encoding="utf-8",
    )
    (logs / "events.out.tfevents.fixture").write_bytes(b"fake TensorBoard contract payload")
    marker = output / "descendant.json"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import json,os,signal,time,psutil;from pathlib import Path;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "Path(os.environ['LW_DESCENDANT_MARKER']).write_text(json.dumps({'pid':os.getpid(),"
                "'createdAt':psutil.Process().create_time()}));time.sleep(120)"
            ),
        ],
        env=os.environ | {"LW_DESCENDANT_MARKER": str(marker)},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists() and child.poll() is None
    if args.hold:
        while True:
            time.sleep(0.1)


if __name__ == "__main__":
    main()
