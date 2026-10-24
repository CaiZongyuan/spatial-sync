"""A frozen script version is reviewed by the existing training Plan without fake Jobs."""

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.training.inputs import pack_package
from tests.fixtures.script_program import program_directory
from tests.fixtures.training_task import task_archive, task_directory, training_plan


def prepare(client, tmp_path):
    program, _ = program_directory(tmp_path)
    response = client.post(
        "/api/v1/programs", content=pack_package(program), headers={"Content-Type": "application/x-tar"}
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()
    task = task_directory(tmp_path / "task", "script-plan")
    response = client.post(
        "/api/v1/tasks", content=task_archive(task), headers={"Content-Type": "application/x-tar"}
    )
    assert response.status_code == 201, response.text
    body = training_plan(response.json()["id"], budget={"gpuHours": 0.0, "episodes": 2})
    for name in ("algorithm", "model", "hyperparameters"):
        body.pop(name)
    body.update(program={"id": snapshot["id"], "contentHash": snapshot["contentHash"]}, jobs={"seeds": []})
    return body, snapshot


def test_existing_training_plan_approves_exact_script_bytes_without_training_metadata_or_jobs(tmp_path):
    with TestClient(create_app()) as client:
        body, snapshot = prepare(client, tmp_path)
        submitted = client.post("/api/v1/plans", json=body)
        assert submitted.status_code == 201, submitted.text
        plan = submitted.json()
        assert plan["program"] == body["program"]
        assert all(plan[name] == {} for name in ("algorithm", "model", "hyperparameters"))
        assert plan["jobs"] == {"seeds": []} and plan["budget"]["gpuHours"] == 0
        frozen_before = client.get("/api/v1/programs/probe/1/archive").content
        approved = client.post(
            f"/api/v1/approvals/{plan['approvalId']}/decision",
            headers={"X-Actor": "user:code-reviewer"},
            json={"decision": "approve"},
        )
        assert approved.status_code == 200, approved.text
        assert client.get("/api/v1/plans/" + plan["id"]).json()["status"] == "approved"
        assert client.get("/api/v1/programs/probe/1/archive").content == frozen_before
        linked = client.get("/api/v1/programs/probe/1").json()["plans"]
        assert linked == [
            {"id": plan["id"], "revision": 1, "approvalId": plan["approvalId"], "status": "approved"}
        ]
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "planRevision": 1, "seed": 42})
        assert job.status_code == 422
        assert client.get("/api/v1/jobs").json()["jobs"] == []
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
        assert client.get("/api/v1/programs/probe/1").json()["contentHash"] == snapshot["contentHash"]


@pytest.mark.parametrize("case", ["hash", "missing", "training", "jobs", "gpu", "legacy"])
def test_program_plan_rejects_unreviewable_or_fake_training_intent(tmp_path, case):
    with TestClient(create_app()) as client:
        body, _ = prepare(client, tmp_path)
        if case == "hash":
            body["program"]["contentHash"] = "0" * 64
        elif case == "missing":
            body["program"]["id"] = "program/robot_arm.script.probe@99"
        elif case == "training":
            body["algorithm"] = {"name": "PPO"}
        elif case == "jobs":
            body["jobs"]["seeds"] = [42]
        elif case == "gpu":
            body["budget"]["gpuHours"] = 1.0
        else:
            body.pop("program")
        response = client.post("/api/v1/plans", json=body)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_plan"
        assert client.get("/api/v1/jobs").json()["jobs"] == []


def test_program_bytes_are_reverified_at_human_approval(tmp_path):
    with TestClient(create_app()) as client:
        body, snapshot = prepare(client, tmp_path)
        response = client.post("/api/v1/plans", json=body)
        assert response.status_code == 201, response.text
        plan = response.json()
        _, stored = client.app.state.programs.resolve(snapshot["id"])
        (stored / "controller.py").write_bytes(b"raise RuntimeError('changed code after review')\n")
        result = client.post(f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"})
        assert result.status_code == 409, result.text
        assert client.get("/api/v1/plans/" + plan["id"]).json()["status"] == "submitted"
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
