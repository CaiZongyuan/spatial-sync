"""ONNX external files are part of the complete uploaded policy identity."""

import hashlib
import json

from fastapi.testclient import TestClient
from test_training_jobs import HEARTBEAT, NODE, TOKEN, approve, heartbeat, setup_plan

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.training.inputs import pack_package


def test_artifact_upload_rejects_missing_or_changed_declared_onnx_data(tmp_path):
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task))
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        changed_hardware = heartbeat(plan, task)
        changed_hardware["gpus"][0]["name"] = "later telemetry must not rewrite assigned hardware"
        assert client.post(HEARTBEAT, headers=NODE, json=changed_hardware).status_code == 200
        assert client.get(f"/api/v1/jobs/{job['jobId']}").json()["resourceModel"] == job["resourceModel"]
        headers = NODE | {"X-Lease-Id": job["leaseId"], "Content-Type": "application/x-tar"}
        output = tmp_path / "output"
        output.mkdir()
        for name, data in {
            "model_0.pt": b"checkpoint",
            "policy.onnx": b"protobuf",
            "policy.onnx.data": b"weights",
            "events.out.tfevents.fixture": b"events",
            "stdout.log": b"",
            "stderr.log": b"",
        }.items():
            (output / name).write_bytes(data)
        versions = {
            "versions": {name: "fixture" for name in ("isaaclab", "newton", "warp", "torch", "rsl_rl")},
            "hardware": {"device": "cuda", "gpu": {"uuid": job["resourceId"], "name": job["resourceModel"]}},
            **{
                name: job[name]
                for name in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
            },
        }
        (output / "versions.json").write_text(json.dumps(versions), encoding="utf-8")

        def digest(name):
            return hashlib.sha256((output / name).read_bytes()).hexdigest()

        export = {
            "checkpoint": "model_0.pt",
            "checkpointSha256": digest("model_0.pt"),
            "policy": "policy.onnx",
            "policySha256": digest("policy.onnx"),
            "afterLearn": True,
            "iteration": 0,
            "externalData": [{"path": "policy.onnx.data", "size": 7, "sha256": digest("policy.onnx.data")}],
        }
        (output / "export.json").write_text(json.dumps(export), encoding="utf-8")
        route = f"/api/v1/jobs/{job['jobId']}/artifacts"
        for key in ("uuid", "name"):
            versions["hardware"]["gpu"][key] = "different resource"
            (output / "versions.json").write_text(json.dumps(versions), encoding="utf-8")
            wrong = client.post(route, headers=headers, content=pack_package(output))
            assert wrong.status_code == 422 and "assigned resource" in wrong.text
            versions["hardware"]["gpu"][key] = job["resourceId" if key == "uuid" else "resourceModel"]
        (output / "versions.json").write_text(json.dumps(versions), encoding="utf-8")
        assert job["resourceModel"] == "GPU-free contract double"
        original = (output / "policy.onnx.data").read_bytes()
        (output / "policy.onnx.data").unlink()
        missing = client.post(route, headers=headers, content=pack_package(output))
        assert missing.status_code == 422 and missing.json()["detail"]["code"] == "artifacts_incomplete"
        (output / "policy.onnx.data").write_bytes(b"changed")
        changed = client.post(route, headers=headers, content=pack_package(output))
        assert changed.status_code == 422
        assert client.get(f"/api/v1/jobs/{job['jobId']}").json()["artifacts"] == []
        (output / "policy.onnx.data").write_bytes(original)
        assert client.post(route, headers=headers, content=pack_package(output)).status_code == 200
        repeat = client.post(route, headers=headers, content=pack_package(output))
        assert repeat.status_code == 200
        assert any(row["path"] == "policy.onnx.data" for row in repeat.json()["artifacts"])
