"""Real node artifact HTTP channel to immutable CPU-loadable policy candidates."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.policies.inference import CpuPolicy
from packages.policies.observations import ObservationSpec
from tests.fixtures.policy import artifact_archive, model_bytes, observation_spec, policy_task
from tests.fixtures.training_task import training_plan
from tests.gateway.test_training_jobs import HEARTBEAT, NODE, TOKEN, approve, heartbeat


def uploaded_job(client, tmp_path, *, model=None, external=None, node_headers=None, spec=None):
    node_headers = node_headers or NODE
    task = client.post(
        "/api/v1/tasks",
        content=policy_task(tmp_path / "task", spec=spec),
        headers={"Content-Type": "application/x-tar"},
    ).json()
    plan = client.post("/api/v1/plans", json=training_plan(task["id"])).json()
    approve(client, plan["approvalId"])
    client.post(HEARTBEAT, headers=node_headers, json=heartbeat(plan, task))
    job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
    headers = node_headers | {"X-Lease-Id": job["leaseId"]}
    upload = client.post(
        f"/api/v1/jobs/{job['jobId']}/artifacts",
        headers=headers,
        content=artifact_archive(job, model=model, external=external),
    )
    assert upload.status_code == 200, upload.text
    report = {"jobId": job["jobId"], "leaseId": job["leaseId"], "status": "succeeded", "elapsedSeconds": 1.0}
    finished = client.post(HEARTBEAT, headers=node_headers, json=heartbeat(plan, task, [report]))
    assert finished.status_code == 200, finished.text
    return job


def test_policy_freezes_real_onnx_and_provenance_retry_restart_and_detects_tamper(tmp_path):
    settings = Settings(node_token=TOKEN)
    app = create_app(settings)
    with TestClient(app) as client:
        job = uploaded_job(client, tmp_path)
        body = {"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
        created = client.post("/api/v1/policies", json=body)
        assert created.status_code == 201, created.text
        policy = created.json()
        assert policy["status"] == "candidate" and policy["qualified"] is False
        assert policy["evaluations"] == [] and len(policy["missingEvidence"]) == 2
        assert policy["task"] == job["task"] and policy["sceneContentHash"] == job["sceneContentHash"]
        assert not policy["idempotent"]
        assert client.post("/api/v1/policies", json=body).json() == policy | {"idempotent": True}
        saved, package = app.state.policies.resolve(policy["id"])
        spec = ObservationSpec.model_validate(observation_spec())
        assert CpuPolicy(package / saved["onnx"], spec).infer([2.0]).tolist() == [2.0]
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/policies/training-probe/1").json()["contentHash"] == policy["contentHash"]
        assert len(client.get("/api/v1/policies").json()["policies"]) == 1
        root = app.state.manager.library_assets.roots["user"]
        source = root / "jobs" / job["jobId"] / "artifacts/policy.onnx"
        source.write_bytes(b"changed")
        rejected = client.post("/api/v1/policies", json=body)
        assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "policy_source_invalid"
        # The already frozen candidate remains readable independently of its original source bytes.
        assert client.get("/api/v1/policies/training-probe/1").status_code == 200
        (package / "artifacts/policy.onnx").write_bytes(b"changed")
        assert client.get("/api/v1/policies/training-probe/1").status_code == 409


def test_policy_freezes_verified_external_weights_and_rejects_manifest_omissions(tmp_path):
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        job = uploaded_job(
            client,
            tmp_path,
            model=model_bytes(external_path="weights.bin"),
            external={"weights.bin": np.ones((1, 1), dtype=np.float32).tobytes()},
        )
        created = client.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
        )
        assert created.status_code == 201, created.text
        record, package = app.state.policies.resolve(created.json()["id"])
        assert (package / "artifacts/weights.bin").is_file()
        export = [{"path": "weights.bin"}]
        model = CpuPolicy(
            package / record["onnx"],
            ObservationSpec.model_validate(observation_spec()),
            root=package / "artifacts",
            external=export,
        )
        assert model.infer([4.0]).tolist() == [4.0]


@pytest.mark.parametrize("checkpoint", ["host-path", "job/../../host", "job/C:/weights", "job//weights"])
def test_policy_rejects_host_paths_and_traversal(checkpoint):
    with TestClient(create_app(Settings())) as client:
        result = client.post("/api/v1/policies", json={"checkpoint": checkpoint})
        assert result.status_code == 422, result.text


def test_non_onnx_job_payload_does_not_become_policy(tmp_path):
    with TestClient(create_app(Settings(node_token=TOKEN))) as client:
        job = uploaded_job(client, tmp_path, model=b"explicit fake payload")
        result = client.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
        )
        assert result.status_code == 422
