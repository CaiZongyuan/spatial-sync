"""Numerical engineering fixture, never a trained/qualified contact policy."""

import hashlib
import io
import json
import tarfile

import numpy as np
import onnx
import yaml
from onnx import TensorProto, helper, numpy_helper

from tests.fixtures.training_task import task_archive, task_directory


def observation_spec():
    return {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "q",
                "source": "joint_position",
                "keys": ["/World/Lab/RobotA/joint1"],
                "frame": "world",
                "unit": "rad",
                "shape": [1],
                "offset": [0.0],
                "scale": [2.0],
            }
        ],
        "action": {
            "space": "joint_position_delta",
            "jointKeys": ["/World/Lab/RobotA/joint1"],
            "units": ["rad"],
            "scale": [0.1],
            "lower": [-1.0],
            "upper": [1.0],
            "maxDelta": [0.2],
        },
    }


def model_bytes(size=1, *, external_path=None):
    weight = numpy_helper.from_array(np.ones((size, 1), dtype=np.float32), "weight")
    if external_path:
        onnx.external_data_helper.set_external_data(weight, location=external_path)
        weight.ClearField("raw_data")
        weight.data_location = TensorProto.EXTERNAL
    model = helper.make_model(
        helper.make_graph(
            [helper.make_node("MatMul", ["observations", "weight"], ["actions"])],
            "cpu-engineering-fixture",
            [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, size])],
            [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, 1])],
            [weight],
        ),
        opset_imports=[helper.make_opsetid("", 18)],
    )
    model.ir_version = 10
    return model.SerializeToString()


def policy_task(path, *, spec=None, task=None):
    task_directory(path)
    if task is not None:
        (path / "task.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    (path / "obs_spec.yaml").write_text(yaml.safe_dump(spec or observation_spec()), encoding="utf-8")
    return task_archive(path)


def artifact_archive(job, *, model=None, external=None):
    files = {
        "checkpoints/model_0.pt": b"fixture-not-trained-checkpoint",
        "policy.onnx": model or model_bytes(),
        "stdout.log": b"explicit numerical fixture",
        "stderr.log": b"",
        "events.out.tfevents.cpu": b"fixture",
    }
    versions = {
        "versions": {key: "fixture-only" for key in ("isaaclab", "newton", "warp", "torch", "rsl_rl")},
        "hardware": {"device": "cuda", "gpu": {"uuid": job["resourceId"], "name": job["resourceModel"]}},
        **{
            key: job[key] for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
        },
    }
    export = {
        "checkpoint": "checkpoints/model_0.pt",
        "policy": "policy.onnx",
        "afterLearn": True,
        "iteration": 0,
        "externalData": [],
    }
    for key in ("checkpoint", "policy"):
        export[key + "Sha256"] = hashlib.sha256(files[export[key]]).hexdigest()
    if external:
        files.update(external)
        export["externalData"] = [
            {"path": key, "sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
            for key, value in external.items()
        ]
    files["versions.json"] = json.dumps(versions).encode()
    files["export.json"] = json.dumps(export).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, value in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            archive.addfile(entry, io.BytesIO(value))
    return stream.getvalue()
