"""Author a real alert scene and pin an otherwise unchanged evaluation suite to it."""

import argparse
import io
import shutil
import sys
import tarfile
from pathlib import Path

import yaml

BUILTIN_ROOT = Path(__file__).resolve().parents[2] / "library"
sys.path.insert(0, str(BUILTIN_ROOT.parent))
from packages.library.common import NAME  # noqa: E402 - direct fixture CLI needs this checkout.


def fixture_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name) or not (
        name == "evaluation-alerts" or name.startswith("evaluation-alerts-")
    ):
        raise ValueError("Use a legal evaluation-alerts fixture name")
    return name


def scene_archive(name="evaluation-alerts"):
    name = fixture_name(name)
    package = BUILTIN_ROOT / "scenes/lab-demo/3"
    scene = yaml.safe_load((package / "scene.yaml").read_text(encoding="utf-8"))
    scene["name"] = name
    bindings = yaml.safe_load((package / "devices.yaml").read_text(encoding="utf-8"))
    bindings["scene"] = scene["name"]
    incubator = next(item for item in bindings["entities"] if item.get("key") == "/World/Lab/Incubator")
    incubator["alerts"] = {"evaluationTemperature": {
        "when": {"property": "temperatureC", "gt": 0}, "forSeconds": 0,
        "severity": "critical", "message": "Evaluation temperature is positive",
    }}
    files = {
        "scene.yaml": yaml.safe_dump(scene).encode(),
        "devices.yaml": yaml.safe_dump(bindings).encode(),
        "locations.yaml": (package / "locations.yaml").read_bytes(),
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, value in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return stream.getvalue()


def prepare_suite(root, scene):
    name = fixture_name(scene.name)
    package = root / "suites" / name / str(scene.rev)
    shutil.copytree(BUILTIN_ROOT / "suites/lab-demo-pick-place/1", package)
    suite = yaml.safe_load((package / "suite.yaml").read_text(encoding="utf-8"))
    suite.update(id=f"suite/{name}", scene=scene.id, sceneContentHash=scene.content_hash)
    (package / "suite.yaml").write_text(yaml.safe_dump(suite), encoding="utf-8")
    task = yaml.safe_load((package / "task.yaml").read_text(encoding="utf-8"))
    task["scene"] = scene.id
    (package / "task.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    return f"suite/{name}@{scene.rev}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene-name", default="evaluation-alerts")
    sys.stdout.buffer.write(scene_archive(parser.parse_args().scene_name))
