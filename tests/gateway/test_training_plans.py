"""Task snapshots and human-approved intent need no session, physics engine or GPU."""

import io
import tarfile
from copy import deepcopy

import pytest
import yaml
from fastapi.testclient import TestClient
from scene_support import user_version

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.plans import PlanError
from tests.fixtures.training_task import task_archive, task_directory, training_plan


def push(client, data=None):
    return client.post("/api/v1/tasks", content=data or task_archive(),
                       headers={"Content-Type": "application/x-tar"})


def test_task_push_versions_hashes_and_never_executes_package_python(tmp_path):
    with TestClient(create_app(Settings())) as client:
        package = task_directory(tmp_path / "task")
        first = push(client, task_archive(package))
        assert first.status_code == 201, first.text
        first = first.json()
        assert first["id"] == "task/training-probe@1" and len(first["contentHash"]) == 64
        assert not first["idempotent"]
        second = push(client, task_archive(package)).json()
        assert second == first | {"idempotent": True}
        (package / "probe.py").write_text("raise RuntimeError('new reward')\n", encoding="utf-8")
        third = push(client, task_archive(package)).json()
        assert third["id"] == "task/training-probe@2" and third["contentHash"] != first["contentHash"]
        assert push(client, task_archive(task_directory(tmp_path / "original"))).json()["id"] == first["id"]
        assert client.get("/api/v1/tasks/training-probe/1").json()["contentHash"] == first["contentHash"]
        assert len(client.get("/api/v1/tasks").json()["tasks"]) == 2
        assert client.get("/api/v1/health").json()["sessionCount"] == 0


def test_training_plan_human_approval_revisions_diff_and_restart():
    settings = Settings()
    with TestClient(create_app(settings)) as client:
        assert push(client).status_code == 201
        response = client.post("/api/v1/plans", json=training_plan(), headers={"X-Actor": "agent:trainer"})
        assert response.status_code == 201, response.text
        first = response.json()
        assert first["status"] == "submitted" and first["approvalMode"] == "human"
        messages = client.get("/api/v1/inbox?kinds=plan").json()["messages"]
        assert messages[-1]["data"]["hypotheses"][0]["predict"] == {"metric": "stage.insert",
                                                                    "from": 0.61, "to": ">= 0.80"}
        approval_id = first["approvalId"]
        route = f"/api/v1/approvals/{approval_id}/decision"
        assert client.post(route, json={"decision": "approve"},
                           headers={"X-Actor": "agent:trainer"}).status_code == 403
        decision = client.post(route, json={"decision": "deny", "note": "Need a controlled comparison"})
        assert decision.status_code == 200
        assert client.get(f"/api/v1/plans/{first['id']}").json()["status"] == "rejected"
        revised = client.post(f"/api/v1/plans/{first['id']}/revisions",
                              json=training_plan(goal="Control the comparison")).json()
        assert revised["revision"] == 2
        assert [item["field"] for item in revised["changes"]] == ["goal"]
        assert client.post(route, json={"decision": "approve"}).status_code == 409
        route = f"/api/v1/approvals/{revised['approvalId']}/decision"
        decision = client.post(route, json={"decision": "approve", "note": "Approved CPU-free intent"})
        assert decision.status_code == 200
        assert client.get(f"/api/v1/plans/{first['id']}").json()["status"] == "approved"
        assert client.get(f"/api/v1/plans/{first['id']}?revision=1").json()["status"] == "rejected"
        approval = client.get(f"/api/v1/approvals/{revised['approvalId']}").json()
        assert approval["canRevoke"] and approval["sessionId"] is None
        assert approval["details"]["plan"]["interview"] == training_plan()["interview"]
        assert client.get("/api/v1/plans?kind=operation").json()["plans"] == []
        assert len(client.get("/api/v1/plans?kind=training&status=approved").json()["plans"]) == 1
        assert client.get(f"/api/v1/plans/{first['id']}/steps/1/preview").status_code == 404
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/v1/plans/{first['id']}").json()["status"] == "approved"
        assert client.post(route, json={"decision": "revoke"}).status_code == 200
        assert client.get(f"/api/v1/plans/{first['id']}").json()["status"] == "draft"


@pytest.mark.parametrize("target", [None, "better", ">= nan", True, "<= 1e999"])
def test_unmeasurable_hypothesis_is_invalid_plan(target):
    with TestClient(create_app(Settings())) as client:
        assert push(client).status_code == 201
        document = deepcopy(training_plan())
        document["hypotheses"][0]["predict"]["to"] = target
        response = client.post("/api/v1/plans", json=document)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_plan"
        assert client.get("/api/v1/approvals").json()["pendingCount"] == 0


@pytest.mark.parametrize("name,kind", [("../outside.txt", tarfile.REGTYPE),
                                       ("escape", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE)])
def test_task_archive_rejects_escape_and_filesystem_links(name, kind):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.linkname = "/etc/passwd" if kind != tarfile.REGTYPE else ""
        archive.addfile(entry, io.BytesIO())
    with TestClient(create_app(Settings())) as client:
        response = push(client, stream.getvalue())
        assert response.status_code == 422
        assert client.get("/api/v1/tasks").json()["tasks"] == []


@pytest.mark.parametrize("failure", ["approval_append", "plan_link"])
def test_partial_training_submission_cannot_authorize_and_recovers_after_restart(monkeypatch, failure):
    settings = Settings()
    app = create_app(settings)
    with TestClient(app) as client:
        assert push(client).status_code == 201
        plans, approvals = app.state.manager.training_plans, app.state.manager.approvals
        save_approval, save_plan = approvals._save, plans._save
        if failure == "approval_append":
            def interrupted_approval(record):
                save_approval(record)
                raise OSError("Injected error after durable approval append")
            monkeypatch.setattr(approvals, "_save", interrupted_approval)
        else:
            def interrupted_link(record):
                if record.approvalId:
                    raise PlanError("plan_persistence_unavailable", "Injected link failure", status=503)
                save_plan(record)
            monkeypatch.setattr(plans, "_save", interrupted_link)
        response = client.post("/api/v1/plans", json=training_plan(id="plan-interrupted"))
        assert response.status_code == 503
        monkeypatch.setattr(approvals, "_save", save_approval)
        monkeypatch.setattr(plans, "_save", save_plan)
        orphan = next(iter(approvals.records.values()))
        refused = client.post(f"/api/v1/approvals/{orphan.approvalId}/decision", json={"decision": "approve"})
        assert refused.status_code == 409
        assert client.get("/api/v1/plans/plan-interrupted").json()["status"] == "abandoned"
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/v1/approvals/{orphan.approvalId}").json()["status"] == "unavailable"
        recovered = client.post("/api/v1/plans/plan-interrupted/revisions",
                                json=training_plan(goal="Recovered controlled plan")).json()
        assert recovered["revision"] == 2 and recovered["approvalId"] != orphan.approvalId
        approved = client.post(f"/api/v1/approvals/{recovered['approvalId']}/decision",
                               json={"decision": "approve"})
        assert approved.status_code == 200
        actual = client.get("/api/v1/plans/plan-interrupted").json()
        assert actual["status"] == "approved" and actual["goal"] == "Recovered controlled plan"


def test_task_pins_its_scene_without_blocking_unrelated_deletion_and_damage_stays_closed(tmp_path):
    root = tmp_path / "user-library"
    user_version(root, 4)
    user_version(root, 5)
    package = task_directory(tmp_path / "task")
    task = yaml.safe_load((package / "task.yaml").read_text(encoding="utf-8"))
    task["scene"] = task["evaluation"]["scene"] = "lab-demo@4"
    (package / "task.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    with TestClient(create_app(Settings())) as client:
        pushed = push(client, task_archive(package))
        assert pushed.status_code == 201, pushed.text
        blocked = client.delete("/api/v1/library/scenes/lab-demo/4")
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "version_in_use"
        assert blocked.json()["detail"]["references"] == [
            {"kind": "task", "id": "task/training-probe@1", "reason": "Pinned training Task scene version"},
        ]
        allowed = client.delete("/api/v1/library/scenes/lab-demo/5")
        assert allowed.status_code == 202, allowed.text
        (root / "tasks/training-probe/1/package/probe.py").write_text("changed", encoding="utf-8")
        unknown = client.delete("/api/v1/library/scenes/lab-demo/5")
        assert unknown.status_code == 409
        assert unknown.json()["detail"]["code"] == "reference_inspection_incomplete"
