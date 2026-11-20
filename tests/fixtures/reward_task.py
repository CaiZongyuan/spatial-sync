from pathlib import Path

import yaml

from packages.library.common import BUILTIN_ROOT


def reward_source(path):
    path.mkdir()
    source = BUILTIN_ROOT / "tasks/microduck-stand-up/1/package"
    task = yaml.safe_load((source / "task.yaml").read_bytes())
    task.update(name="reward-revision-probe", observation="retained-observation.yaml", declarative={
        "baseBody": "/World/Lab/Microduck/trunk_base", "episodeSeconds": 3.0,
        "rewards": [{"kind": "upright", "weight": 1.0}],
        "layerB": {"state": "lw.reward-state/1", "rewards": [{"weight": 0.25, "expression": 1}]},
    })
    observation = b"# original observation bytes\n" + (source / "obs_spec.yaml").read_bytes().replace(
        b"lw.obs-spec/1", b"lw.obs-spec/2", 1,
    )
    (path / "task.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    (path / task["observation"]).write_bytes(observation)
    return task, observation


if __name__ == "__main__":
    import sys
    from tempfile import TemporaryDirectory

    from packages.training.inputs import pack_package

    with TemporaryDirectory() as directory:
        root = Path(directory) / "source"
        reward_source(root)
        sys.stdout.buffer.write(pack_package(root))
