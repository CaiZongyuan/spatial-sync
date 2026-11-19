import io
import json
import math
import subprocess
import sys
import sysconfig
import zipfile

import numpy as np
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import BUILTIN_ROOT, content_hash
from packages.policies.inference import CpuPolicy
from packages.policies.observations import ObservationSpec, joint_targets
from tests.fixtures.policy import model_bytes, observation_spec
from tests.fixtures.training_task import task_document
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN


def test_export_runs_outside_platform_with_external_tensor_and_raw_action_history(tmp_path):
    app = create_app(Settings(node_token=TOKEN))
    spec_doc = observation_spec()
    hinge = spec_doc["action"]["jointKeys"][0]
    body = "/World/Lab/RobotA"
    spec_doc["observations"] = [
        {
            "name": "gravity",
            "source": "projected_gravity",
            "key": body,
            "unit": "1",
            "shape": [3],
            "offset": [0, 0, 0],
            "scale": [1, 1, 1],
        },
        {
            "name": "previous",
            "source": "previous_action",
            "frame": "world",
            "unit": "1",
            "shape": [1],
            "offset": [0],
            "scale": [1],
        },
    ]
    spec_doc["schema"] = "lw.obs-spec/2"
    spec_doc["observations"].extend(
        [
            {
                "name": "phase",
                "source": "motion_phase",
                "frame": "world",
                "unit": "1",
                "shape": [2],
                "offset": [0.0, 0.0],
                "scale": [1.0, 1.0],
            },
            {
                "name": "frame",
                "source": "reference_motion_frame",
                "frame": "world",
                "unit": "1",
                "shape": [8],
                "offset": [0.0] * 8,
                "scale": [1.0] * 8,
            },
        ]
    )
    spec_doc["action"].update(scale=[0.5], maxDelta=[0.1])
    spec = ObservationSpec.model_validate(spec_doc)
    motion = {
        "asset": "robot/fr3@1",
        "assetContentHash": content_hash(BUILTIN_ROOT / "assets/robot/fr3/1"),
        "jointKeys": ["/Asset/joint1"],
        "loop": True,
        "license": "CC0-1.0",
        "frames": [
            {"time": 0, "position": [0, 0, 0], "orientation": [0, 0, 0, 1], "joints": [-0.2]},
            {
                "time": 1,
                "position": [1, 0, 0],
                "orientation": [0, 0, -math.sqrt(3) / 2, -0.5],
                "joints": [0.6],
            },
        ],
    }
    task = task_document() | {"referenceMotion": motion}
    with TestClient(app) as client:
        job = uploaded_job(
            client,
            tmp_path,
            model=model_bytes(14, external_path="weights.bin"),
            external={"weights.bin": np.array([1.0] * 4 + [0.0] * 10, dtype=np.float32).tobytes()},
            spec=spec_doc,
            task_document=task,
        )
        registered = client.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
        )
        assert registered.status_code == 201, registered.text
        response = client.get("/api/v1/policies/training-probe/1/export")
        assert response.status_code == 200, response.text
        model, package = app.state.policies.resolve(registered.json()["id"])
        model_path = package / model["onnx"]
        export = json.loads((package / "artifacts/export.json").read_bytes())
        platform = CpuPolicy(model_path, spec, root=package / "artifacts", external=export["externalData"])
    root = tmp_path / "standalone"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["schema"] == "lw.policy-export/1"
    assert manifest["sourceCalibration"]["status"] == "unavailable"
    assert manifest["qualification"]["status"] == "unverified"
    assert (root / "model/weights.bin").is_file()
    assert (root / "infer.py").is_file()
    state = {
        "motionElapsed": 0.25,
        "joints": {hinge: {"q": -0.92, "qd": 0.0}},
        "bodies": {body: {"q": [math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)], "p": [0.0, 0.0, 0.0]}},
    }
    requests = [{"state": state}, {"state": state}, {"state": state, "reset": True}]
    (root / "samples.json").write_text(json.dumps(requests))
    isolated = """
import importlib.util, runpy, sys
sys.path.extend(sys.argv[1:3])
assert importlib.util.find_spec("packages") is None
sys.argv[:] = ["infer.py", "samples.json"]
runpy.run_path("infer.py", run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", isolated, sysconfig.get_path("purelib"), str(root)],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    rows = json.loads(result.stdout)
    previous = np.zeros(1)
    for index, (row, sample) in enumerate(zip(rows, requests, strict=True)):
        if sample.get("reset"):
            previous.fill(0)
        vector = spec.assemble(state, previous_action=previous, motion=motion)
        np.testing.assert_allclose(
            row["observation"][4:],
            [1, 0, 0.25, 0, 0, 0, 0, 0.2588190451, 0.9659258263, 0],
            rtol=0.0,
            atol=1e-5,
        )
        raw = platform.infer(vector)
        targets = joint_targets(spec, [-0.92], raw)
        np.testing.assert_allclose(row["observation"], vector, rtol=0.0, atol=1e-5)
        np.testing.assert_allclose(row["raw"], raw, rtol=0.0, atol=1e-5)
        np.testing.assert_allclose(row["targets"], targets, rtol=0.0, atol=1e-5)
        np.testing.assert_allclose(raw, [-2 if index == 1 else -1], rtol=0.0, atol=1e-5)
        np.testing.assert_allclose(targets, [-1], rtol=0.0, atol=1e-5)
        previous = raw.copy()
