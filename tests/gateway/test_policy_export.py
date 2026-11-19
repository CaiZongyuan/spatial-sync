import hashlib
import io
import json
import math
import subprocess
import sys
import sysconfig
import zipfile

import numpy as np
import pytest
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


@pytest.mark.parametrize("offset", [False, True], ids=["delta", "offset-null"])
def test_export_runs_outside_platform_with_external_tensor_and_raw_action_history(tmp_path, offset):
    app = create_app(Settings(node_token=TOKEN))
    spec_doc = observation_spec()
    hinge = spec_doc["action"]["jointKeys"][0]
    body = "/World/Lab/RobotA"
    if offset:
        hinge = body + "/fr3/fr3_joint1"
        spec_doc["action"]["jointKeys"] = [hinge]
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
    spec_doc["observations"].append({
        "name": "command", "source": "command", "frame": "body", "unit": "1",
        "shape": [1], "offset": [0.], "scale": [1.], "lower": [.1], "upper": [.1],
    })
    spec_doc["action"].update(scale=[0.5], maxDelta=[0.1])
    if offset:
        spec_doc["action"].update(space="joint_position_offset", defaultPosition=[.2], maxDelta=None)
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
    task["episode"]["commandEnvelope"] = [[.1, .1]]
    task["control"]["action"] = spec_doc["action"]["space"]
    with TestClient(app) as client:
        job = uploaded_job(
            client,
            tmp_path,
            model=model_bytes(15, external_path="weights.bin"),
            external={"weights.bin": np.array([1.0] * 4 + [0.0] * 11, dtype=np.float32).tobytes()},
            spec=spec_doc,
            task_document=task,
        )
        registered = client.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoint-origin.json"}
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
    calibration = manifest["sourceCalibration"]
    if offset:
        assert calibration["status"] == "verified"
        assert calibration["baseTransform"] == np.eye(4).ravel().tolist()
        assert calibration["joints"] == [
            {"jointKey": "/Asset/fr3_joint1", "sourceName": "fr3_joint1",
             "sign": 1, "zeroOffset": 0, "unit": "rad"},
        ]
        mapping_bytes = (root / calibration["mappingPath"]).read_bytes()
        assert hashlib.sha256(mapping_bytes).hexdigest() == calibration["mappingSha256"]
        mapping = json.loads(mapping_bytes)
        assert [row["sourceName"] for row in mapping["mapping"]["joints"]] == [
            *(f"fr3_joint{i}" for i in range(1, 8)), "fr3_finger_joint1", "fr3_finger_joint2",
        ]
        asset = next(row for row in manifest["provenance"] if row["id"] == calibration["asset"])
        assert asset["contentHash"] == calibration["assetContentHash"]
        assert {path.rsplit("/", 1)[-1] for path in asset["documents"]} == {"LICENSE", "provenance.json"}
        # A reference client can also consume explicit null from an older manifest.
        assert "maxDelta" not in manifest["observation"]["action"]
        manifest["observation"]["action"]["maxDelta"] = None
        (root / "manifest.json").write_text(json.dumps(manifest))
    else:
        assert calibration["status"] == "unavailable"
    assert manifest["qualification"]["status"] == "unverified"
    assert (root / "model/weights.bin").is_file()
    assert (root / "infer.py").is_file()
    state = {
        "motionElapsed": 0.25,
        "joints": {hinge: {"q": -0.92, "qd": 0.0}},
        "bodies": {body: {"q": [math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)], "p": [0.0, 0.0, 0.0]}},
    }
    command = [float(np.float32(.1))]
    sample = {"state": state, "command": command}
    requests = [sample, sample, sample | {"reset": True}]
    (root / "samples.json").write_text(json.dumps(requests))
    isolated = """
import importlib.util, runpy, sys
sys.path.extend(sys.argv[1:3])
assert importlib.util.find_spec("packages") is None
sys.argv[:] = ["infer.py", "samples.json"]
runpy.run_path("infer.py", run_name="__main__")
"""
    standalone = [sys.executable, "-I", "-S", "-c", isolated, sysconfig.get_path("purelib"), str(root)]
    result = subprocess.run(
        standalone,
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
        vector = spec.assemble(state, command=command, previous_action=previous, motion=motion)
        np.testing.assert_allclose(
            row["observation"][4:],
            [1, 0, 0.25, 0, 0, 0, 0, 0.2588190451, 0.9659258263, 0, 0.1],
            rtol=0.0,
            atol=1e-5,
        )
        raw = platform.infer(vector)
        targets = joint_targets(spec, [-0.92], raw)
        np.testing.assert_allclose(row["observation"], vector, rtol=0.0, atol=1e-5)
        np.testing.assert_allclose(row["raw"], raw, rtol=0.0, atol=1e-5)
        np.testing.assert_allclose(row["targets"], targets, rtol=0.0, atol=1e-5)
        np.testing.assert_allclose(raw, [-2 if index == 1 else -1], rtol=0.0, atol=1e-5)
        expected = (-.8 if index == 1 else -.3) if offset else -1.
        np.testing.assert_allclose(targets, [expected], rtol=0.0, atol=1e-5)
        if offset:
            assert row["sourceTargets"] == {"fr3_joint1": pytest.approx(expected)}
        previous = raw.copy()
    out_of_bounds = [float(np.nextafter(np.float32(.1), np.float32(1.)))]
    (root / "samples.json").write_text(json.dumps([sample | {"command": out_of_bounds}]))
    rejected = subprocess.run(standalone, cwd=root, capture_output=True, text=True, timeout=15)
    assert rejected.returncode != 0
    assert "Command outside the declared envelope" in rejected.stderr
