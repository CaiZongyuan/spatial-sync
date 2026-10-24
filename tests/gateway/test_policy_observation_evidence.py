"""Authenticated Job artifact → independent comparison → immutable check/source readback."""

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.fixtures.policy import observation_spec
from tests.fixtures.policy_evidence import observation_artifacts, sampler_task
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN


def inputs(client, tmp_path, monkeypatch):
    runner = sampler_task(tmp_path / "task")
    monkeypatch.setenv("LW_TASK_PACKAGE", str(tmp_path / "task"))
    spec = observation_spec()
    spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = ["/World/Lab/RobotA/fr3/fr3_joint1"]
    job = uploaded_job(
        client,
        tmp_path,
        spec=spec,
        artifact_factory=lambda job: observation_artifacts(
            job,
            tmp_path / "output",
            runner,
        ),
    )
    created = client.post("/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"})
    assert created.status_code == 201, created.text
    return job, created.json(), "/api/v1/policies/training-probe/1/observation-checks"


def test_checks_and_actual_hook_contract_are_frozen_retryable_and_survive_restart(tmp_path, monkeypatch):
    settings = Settings(node_token=TOKEN)
    app = create_app(settings)
    with TestClient(app) as client:
        _, policy, route = inputs(client, tmp_path, monkeypatch)
        request = {"artifact": "observations/samples.json"}
        created = client.post(route, json=request)
        assert created.status_code == 201, created.text
        check = created.json()
        assert check["result"]["numericalPass"] and check["result"]["samples"] == 256
        assert check["result"]["independentTrainingSemantics"] == "not_verified_by_numerical_comparison"
        assert check["qualified"] is False and check["sourceVerification"]["status"] == "unverified"
        assert client.post(route, json=request).json()["idempotent"]
        verification = client.post(
            route + "/" + check["id"] + "/verify-source", json={"artifact": "observations/capture.json"}
        )
        assert verification.status_code == 200, verification.text
        verified = verification.json()["sourceVerification"]
        assert verified["status"] == "capture_contract_verified" and verified["trainerProcessExitCode"] == 0
        assert verified["taskModule"] == "controlled_policy_dataset" and verified["hookStatus"] == "returned"
        assert verified["realTrainingAndIndependentSemantics"] == "not_asserted_by_source_verification"
        assert verified["qualified"] is False
        assert client.post(
            route + "/" + check["id"] + "/verify-source", json={"artifact": "observations/capture.json"}
        ).json()["idempotent"]
        failed = client.post(route, json={"artifact": "observations/failed.json"})
        assert failed.status_code == 201 and not failed.json()["result"]["numericalPass"]
        assert failed.json()["qualified"] is False
        assert len(client.get(route).json()["checks"]) == 2
        unchanged = client.get("/api/v1/policies/training-probe/1").json()
        assert unchanged["contentHash"] == policy["contentHash"] and unchanged["qualified"] is False
        assert len(unchanged["evidence"]["observationChecks"]) == 2
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
    with TestClient(create_app(settings)) as client:
        read = client.get(route + "/" + check["id"])
        assert (
            read.status_code == 200
            and read.json()["sourceVerification"]["status"] == "capture_contract_verified"
        )
        assert read.json()["result"]["numericalPass"] and read.json()["qualified"] is False


def test_foreign_duplicate_missing_paths_flags_and_source_tamper_do_not_qualify(tmp_path, monkeypatch):
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        job, _, route = inputs(client, tmp_path, monkeypatch)
        check = client.post(route, json={"artifact": "observations/samples.json"}).json()
        for artifact in (
            "observations/duplicate.json",
            "observations/foreign.json",
            "missing.json",
            "../host",
        ):
            response = client.post(route, json={"artifact": artifact})
            assert response.status_code == 422, response.text
        assert (
            client.post(route, json={"artifact": "observations/samples.json", "qualified": True}).status_code
            == 422
        )
        source = app.state.manager.library_assets.roots["user"] / "jobs" / job["jobId"] / "artifacts"
        (source / "observations/samples.json").write_bytes(b"changed original source")
        assert client.post(route, json={"artifact": "observations/samples.json"}).status_code == 422
        assert client.get(route + "/" + check["id"]).json()["result"]["numericalPass"]
        parent = app.state.policy_evidence._parent("policy/training-probe@1")
        (parent / check["id"] / "package/samples.json").write_bytes(b"changed frozen evidence")
        assert client.get(route + "/" + check["id"]).status_code == 409
