"""A static downstream Task fixture; importing its Python intentionally fails."""

import io
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml


def task_document(name="training-probe"):
    return {
        "name": name, "title": "FR3 insert probe", "scene": "robot-and-tubes@2",
        "robot": "/World/Lab/RobotA", "implements": "robot-arm/v1.pickPlace",
        "episode": {"params": {"from": "rack.A1", "to": "centrifuge.slot1"}},
        "success": {"all": [
            {"entity": "/World/Lab/Tubes/Tube01", "property": "location",
             "equals": "/World/Lab/Centrifuge/Rotor/Slot1"},
            {"entity": "/World/Lab/RobotA", "property": "gripperWidth", "gte": 0.0799},
            {"entity": "/World/Lab/RobotA", "property": "gripperWidth", "lte": 0.0801},
        ]},
        "control": {"hz": 30, "action": "joint_position_delta"}, "observation": "obs_spec.yaml",
        "isaaclab": {"task_id": "Lab-FR3-Probe-v0", "presets": ["newton_mjwarp"],
                     "sceneInput": "scene-package"},
        "evaluation": {"scene": "robot-and-tubes@2", "episodes": 2, "seeds": [7, 9],
                       "params": {"from": ["rack.A1"], "to": ["centrifuge.slot1"]}},
        "loopBudget": {"gpuHours": 4, "episodes": 100, "days": 2},
    }


def task_directory(path, name="training-probe"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "task.yaml").write_text(yaml.safe_dump(task_document(name)), encoding="utf-8")
    (path / "obs_spec.yaml").write_text("observation: [joint_positions]\naction: joint_position_delta\n",
                                        encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname="training-probe"\nversion="0.1.0"\n'
        '[project.entry-points."isaaclab.tasks"]\nprobe="probe:register"\n', encoding="utf-8",
    )
    (path / "probe.py").write_text('raise RuntimeError("Task package must never execute on push")\n',
                                  encoding="utf-8")
    return path


def task_archive(path=None, name="training-probe"):
    def pack(root):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            for file in sorted(root.rglob("*")):
                tar.add(file, arcname=file.relative_to(root).as_posix(), recursive=False)
        return stream.getvalue()
    if path:
        return pack(path)
    with tempfile.TemporaryDirectory() as temporary:
        return pack(task_directory(Path(temporary), name))


def training_plan(task="task/training-probe@1", **fields):
    return {
        "kind": "training", "title": "Improve insertion", "goal": "At least 85 successes per 100 episodes",
        "task": task, "algorithm": {"library": "rsl_rl", "name": "PPO", "version": "5.4.1"},
        "model": {"actor": [256, 128], "critic": [256, 128]},
        "hyperparameters": {"num_envs": 4, "max_iterations": 10, "learning_rate": 0.001},
        "jobs": {"seeds": [42, 43]}, "budget": {"gpuHours": 1},
        "evaluation": {"scene": "robot-and-tubes@2", "episodes": 100, "threshold": 85},
        "rationale": "The previous insertion error suggests weak held-goal guidance.",
        "hypotheses": [{"id": "H1.1", "cause": "Insertion guidance is weak", "change": "Increase guidance",
                        "predict": {"metric": "stage.insert", "from": 0.61, "to": ">= 0.80"}}],
        "notebookRead": [], "interview": "Question: target?\nRecommendation: >= 0.80.\nAnswer: yes.",
        **fields,
    }


if __name__ == "__main__":
    sys.stdout.buffer.write(task_archive(name=sys.argv[1] if len(sys.argv) > 1 else "training-probe"))
