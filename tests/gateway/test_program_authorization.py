"""Metadata-only code authorization binds the current human Plan before any Source effect."""

import math

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.info.plans import PlanError
from packages.programs.authorization import require_program_approval, verify_program_approval
from tests.fixtures.embodied_evaluation import native_suite
from tests.gateway.test_program_plans import prepare


def inputs(client, tmp_path):
    body, snapshot = prepare(client, tmp_path)
    submitted = client.post("/api/v1/plans", json=body)
    assert submitted.status_code == 201, submitted.text
    plan = submitted.json()
    manager = client.app.state.manager
    scene = manager.library.resolve(body["evaluation"]["scene"], launch=True, source="newton")
    suite = native_suite(manager, scene)
    return manager, scene, suite, plan, snapshot, body


def test_program_approval_is_exact_current_and_reverified_without_import_or_source(tmp_path):
    def forbidden(*_):
        raise AssertionError("Code authorization must not start a Source")

    with TestClient(create_app(source_factory=forbidden)) as client:
        manager, scene, suite, plan, snapshot, _ = inputs(client, tmp_path)
        assert client.post(
            "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
        ).status_code == 200
        proof, actual = require_program_approval(manager, snapshot["id"], suite, scene,
                                                 plan_id=plan["id"], plan_revision=1)
        assert actual["contentHash"] == snapshot["contentHash"]
        assert proof.program.id == snapshot["id"] and proof.program.contentHash == snapshot["contentHash"]
        assert proof.approvalId == plan["approvalId"] and proof.planRevision == 1
        assert proof.task == plan["task"] and len(proof.taskContentHash) == 64
        assert verify_program_approval(manager, proof, suite, scene) == proof
        assert not hasattr(math, "_lw_uploaded_program_marker")
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
        assert client.post(
            "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "revoke"}
        ).status_code == 200
        with pytest.raises(PlanError, match="approved"):
            verify_program_approval(manager, proof, suite, scene)


@pytest.mark.parametrize("case", ["missing", "pending", "denied", "revision", "artifact", "source", "target"])
def test_missing_or_changed_program_intent_never_borrows_another_approval(tmp_path, case):
    with TestClient(create_app()) as client:
        manager, scene, suite, plan, snapshot, body = inputs(client, tmp_path)
        if case == "denied":
            assert client.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "deny"}
            ).status_code == 200
        elif case != "pending":
            assert client.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code == 200
        requested, plan_id, revision = snapshot["id"], plan["id"], 1
        if case == "missing":
            plan_id = None
        elif case == "revision":
            response = client.post("/api/v1/plans/" + plan["id"] + "/revisions", json=body)
            assert response.status_code == 201, response.text
        elif case == "artifact":
            requested = "program/robot_arm.script.another@1"
        elif case == "source":
            suite.metadata["source"] = "synthetic"
        elif case == "target":
            suite.task["robot"] = "/World/Lab/AnotherRobot"
        with pytest.raises(PlanError):
            require_program_approval(
                manager, requested, suite, scene, plan_id=plan_id, plan_revision=revision
            )
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
        assert not hasattr(math, "_lw_uploaded_program_marker")
