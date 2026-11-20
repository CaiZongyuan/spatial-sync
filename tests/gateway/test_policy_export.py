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
from packages.library.common import BUILTIN_ROOT, content_hash, document
from packages.policies import export as policy_exports
from packages.policies.inference import CpuPolicy
from packages.policies.observations import ObservationSpec, joint_targets
from tests.fixtures.policy import model_bytes, observation_spec
from tests.fixtures.training_task import task_document
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN


def standalone_command(root):
    isolated = """
import importlib.util, runpy, sys
sys.path.extend(sys.argv[1:3])
assert importlib.util.find_spec("packages") is None
sys.argv[:] = ["infer.py", "samples.json"]
runpy.run_path("infer.py", run_name="__main__")
"""
    return [sys.executable, "-I", "-S", "-c", isolated, sysconfig.get_path("purelib"), str(root)]


@pytest.mark.parametrize("name, command", [
    ("microduck-stand-up", None), ("microduck-bow", [0.2]), ("microduck-walk-to", [0.1, 0., 0.]),
])
def test_builtin_microduck_export_matches_pinned_mjcf_calibration(tmp_path, name, command):
    with TestClient(create_app()) as client:
        response = client.get(f"/api/v1/policies/{name}/1/export")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_bytes())
    calibration = manifest["sourceCalibration"]
    assert calibration["status"] == "verified"
    assert calibration["asset"] == "robot/microduck@1"
    asset_hash = "daf5e41b880367a5258bbcb97c15b6333b98d31595ef57f2a92e79f2c046f25b"
    assert calibration["assetContentHash"] == asset_hash
    mapping_bytes = (tmp_path / calibration["mappingPath"]).read_bytes()
    assert hashlib.sha256(mapping_bytes).hexdigest() == calibration["mappingSha256"]
    mapping = json.loads(mapping_bytes)
    assert mapping["sourceSha256"] == "99d23867e46e9d4748ff1398016b850f09fb6cf02a558c0272743b39ce3acf15"
    repository = "https://github.com/pollen-robotics/microduck_rl"
    revision = "2b25a48b08f1f17bc38c90bb03144c81fbd9ed07"
    entrypoint = "src/mjlab_microduck/robot/microduck/robot_groundcontact.xml"
    assert (mapping.get("sourceRepository"), mapping["sourceRevision"], mapping["sourceEntrypoint"]) == (
        repository, revision, entrypoint,
    )
    asset = next(row for row in manifest["provenance"] if row["id"] == calibration["asset"])
    assert asset["contentHash"] == asset_hash
    provenance_path = next(path for path in asset["documents"] if path.endswith("/provenance.json"))
    frozen = json.loads((tmp_path / provenance_path).read_bytes())
    assert mapping["sourceSha256"] == frozen["sourceFiles"][f"microduck_rl/{entrypoint}"]["sha256"]
    source = asset["source"]
    assert (source["repository"], source["revision"], source["entrypoint"], source["sha256"]) == (
        repository, revision, entrypoint, mapping["sourceSha256"],
    )
    project, models = source["licenses"]
    assert (project["scope"], project["license"], project["sourcePath"]) == (
        "project", "Apache-2.0", "LICENSE",
    )
    assert (models["scope"], models["license"], models["sourcePath"]) == (
        "3D model files", "Creative Commons BY-SA-NC", "README.md",
    )
    for license_entry in source["licenses"]:
        assert license_entry["document"] in asset["documents"]
        data = (tmp_path / license_entry["document"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == license_entry["sha256"]
    project_bytes = (tmp_path / project["document"]).read_bytes()
    assert hashlib.sha256(project_bytes).hexdigest() == (
        "2d3fd9ffffa70a4307498ec630b14a6b4731e010f8ddda92686eaf65733c2dfe"
    )
    assert (tmp_path / models["document"]).read_text() == (
        "## License\n\n"
        "This project is licensed under the Apache 2.0 License. "
        "See the [LICENSE](LICENSE) file for details.\n"
        "3D model files are licensed under Creative Commons BY-SA-NC.\n"
    )
    assert "referenceMotion" not in manifest
    # MuJoCo actuator order, verified against the pinned USD joint frames at zero and both signs.
    source_names = (
        "left_hip_yaw", "left_hip_roll", "left_hip_pitch", "left_knee", "left_ankle",
        "neck_pitch", "head_pitch", "head_yaw", "head_roll",
        "right_hip_yaw", "right_hip_roll", "right_hip_pitch", "right_knee", "right_ankle",
    )
    assert calibration["joints"] == [
        {"jointKey": f"/Asset/joints/{joint}", "sourceName": joint,
         "sign": 1, "zeroOffset": 0., "unit": "rad"}
        for joint in source_names
    ]
    spec = ObservationSpec.model_validate(manifest["observation"])
    assert spec.schema_version == "lw.obs-spec/1"
    robot = "/World/Lab/Microduck"
    keys = [f"{robot}/joints/{joint}" for joint in source_names]
    assert spec.action.jointKeys == keys
    # Row-major asset-from-source transform, using the actual authored poses of trunk_base.
    np.testing.assert_allclose(calibration["baseTransform"], [
        0.9961802142, 0., -0.0873211364, 0.0104785364,
        0., 1., 0., 0.,
        0.0873211364, 0., 0.9961802142, -0.0017696459,
        0., 0., 0., 1.,
    ], rtol=0., atol=1e-9)
    state = {
        "joints": {key: {"q": value, "qd": 0.} for key, value in
                   zip(keys, spec.action.defaultPosition, strict=True)},
        "bodies": {f"{robot}/trunk_base": {"q": [0., 0., 0., 1.], "w": [0., 0., 0.]}},
    }
    (tmp_path / "samples.json").write_text(json.dumps([{"state": state, "command": command}]))
    completed = subprocess.run(
        standalone_command(tmp_path), cwd=tmp_path, capture_output=True, text=True, check=True, timeout=15,
    )
    row, = json.loads(completed.stdout)
    vector = spec.assemble(state, previous_action=np.zeros(14), command=command)
    policy = BUILTIN_ROOT / "policies" / name / "1/package/artifacts/policy.onnx"
    raw = CpuPolicy(policy, spec).infer(vector)
    np.testing.assert_allclose(row["raw"], raw, rtol=0., atol=1e-5)
    targets = joint_targets(spec, spec.action.defaultPosition, raw)
    limits = np.asarray([channel["physicalLimits"] for channel in manifest["actuators"]])
    targets = np.clip(targets, limits[:, 0], limits[:, 1])
    assert row["sourceTargets"] == pytest.approx(dict(zip(source_names, targets, strict=True)), abs=1e-5)


@pytest.mark.parametrize("revision, unit", [(1, "rad"), (2, "rad"), (1, "m")])
def test_microduck_export_reuses_asset_calibration_in_patrol(tmp_path, revision, unit):
    published = BUILTIN_ROOT / "tasks/microduck-stand-up/1/package"
    task = document(published / "task.yaml")
    task.pop("mjlab")
    task["isaaclab"] = task_document()["isaaclab"]
    scene = f"microduck-lab-patrol@{revision}"
    task.update(name="patrol-duck-export", scene=scene, robot="/World/Lab/PatrolDuck")
    task["evaluation"]["scene"] = scene
    spec = document(BUILTIN_ROOT / "policies/microduck-stand-up/1/package/obs_spec.yaml")
    spec = json.loads(json.dumps(spec).replace("/World/Lab/Microduck/", task["robot"] + "/"))
    spec["action"]["units"][0] = unit
    model = (BUILTIN_ROOT / "policies/microduck-stand-up/1/package/artifacts/policy.onnx").read_bytes()
    with TestClient(create_app(Settings(node_token=TOKEN))) as client:
        job = uploaded_job(
            client, tmp_path, model=model, spec=spec, task_document=task,
            plan_overrides={"evaluation": {"scene": scene, "episodes": 4, "threshold": 4}},
        )
        created = client.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoint-origin.json"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["scene"] == scene
        patrol_export = client.get("/api/v1/policies/patrol-duck-export/1/export")
        arena_export = client.get("/api/v1/policies/microduck-stand-up/1/export")
    assert patrol_export.status_code == arena_export.status_code == 200, patrol_export.text
    with (zipfile.ZipFile(io.BytesIO(patrol_export.content)) as patrol,
          zipfile.ZipFile(io.BytesIO(arena_export.content)) as arena):
        patrol_manifest = json.loads(patrol.read("manifest.json"))
        arena_manifest = json.loads(arena.read("manifest.json"))
        calibration = patrol_manifest["sourceCalibration"]
        if unit == "m":
            assert calibration["status"] == "unavailable"
            assert calibration["joints"] == [] and calibration["baseTransform"] is None
            assert "source-calibration.json" not in patrol.namelist()
        else:
            assert calibration == arena_manifest["sourceCalibration"]
        assert len(patrol_manifest["actuators"]) == 14
        assert patrol_manifest["actuators"][0]["jointKey"] == task["robot"] + "/joints/left_hip_yaw"
        asset = next(row for row in patrol_manifest["provenance"] if row["id"] == "robot/microduck@1")
        assert {item["license"] for item in asset["source"]["licenses"]} == {
            "Apache-2.0", "Creative Commons BY-SA-NC",
        }
        for item in asset["source"]["licenses"]:
            assert patrol.read(item["document"]) == arena.read(item["document"])


@pytest.mark.parametrize("pin", ["asset", "assetContentHash", "sourceSha256"])
def test_builtin_export_omits_calibration_without_matching_provenance(tmp_path, monkeypatch, pin):
    directory = tmp_path / "retained-calibrations"
    directory.mkdir()
    name = "microduck-1.json"
    record = json.loads((BUILTIN_ROOT / "retained-calibrations" / name).read_bytes())
    (directory / name).write_text(json.dumps(record | {pin: "0" * 64}))
    monkeypatch.setattr(policy_exports, "BUILTIN_ROOT", tmp_path)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/policies/microduck-stand-up/1/export")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        calibration = json.loads(archive.read("manifest.json"))["sourceCalibration"]
        assert "source-calibration.json" not in archive.namelist()
    assert calibration["status"] == "unavailable"
    assert calibration["joints"] == []
    assert calibration["baseTransform"] is None


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
    assert {"id": "reference-motion.json", "license": "CC0-1.0"} in manifest["provenance"]
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
    standalone = standalone_command(root)
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
