"""Script evaluations use exact human intent and the existing queue without starting a Source here."""

import math

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from tests.fixtures.embodied_evaluation import native_suite
from tests.gateway.test_program_plans import prepare


def inputs(client, tmp_path, *, approved=True):
    body, program = prepare(client, tmp_path)
    plan = client.post("/api/v1/plans", json=body).json()
    if approved:
        assert (
            client.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
    manager = client.app.state.manager
    scene = manager.library.resolve(body["evaluation"]["scene"], launch=True, source="newton")
    suite = native_suite(manager, scene)
    manager.evaluations.suites.refresh()
    # Hold the existing admission slot: metadata admission is the fact under test.
    manager.evaluations._ordinary_creates = 1
    return manager, plan, program, suite


def test_known_script_identity_and_code_review_survive_unavailable_comparison_proof(tmp_path):
    calls = []

    def forbidden(*args):
        calls.append(args)
        raise AssertionError("Busy evaluator cannot start a Source")

    with TestClient(create_app(source_factory=forbidden)) as client:
        manager, plan, program, suite = inputs(client, tmp_path)
        manager.evaluations.identity_producer = lambda *_args, **_kwargs: None
        response = client.post(
            "/api/v1/evaluations",
            json={"subject": program["id"], "suite": suite.id, "planId": plan["id"], "planRevision": 1},
        )
        assert response.status_code == 202, response.text
        public = response.json()
        record = manager.evaluations.store.records[public["evaluationId"]]
        assert record["identitySnapshot"] is None
        assert record["simulationSubject"]["codeApproval"]["approvalId"] == plan["approvalId"]
        assert record["planId"] == plan["id"] and record["planRevision"] == 1
        assert public["candidateIdentity"]["id"] == program["id"]
        for name in ("contentHash", "codeHash", "configHash", "archiveSha256"):
            assert public["candidateIdentity"][name] == program[name]
        assert public["status"] == "queued" and public["attemptedEpisodes"] == 0
        assert "trainingSuccess" not in record and "policySubject" not in record
        assert not calls and not hasattr(math, "_lw_uploaded_program_marker")


@pytest.mark.parametrize("case", ["missing", "pending", "revision", "source"])
def test_script_admission_rejects_without_current_compatible_code_intent(tmp_path, case):
    with TestClient(create_app()) as client:
        manager, plan, program, suite = inputs(client, tmp_path, approved=case != "pending")
        body = {"subject": program["id"], "suite": suite.id, "planId": plan["id"], "planRevision": 1}
        if case == "missing":
            body.pop("planId")
            body.pop("planRevision")
        elif case == "revision":
            body["planRevision"] = 2
        elif case == "source":
            body["suite"] = "suite/lab-demo-pick-place@1"
        result = client.post("/api/v1/evaluations", json=body)
        assert result.status_code in (409, 422), result.text
        expected = {
            "missing": "program_approval_required",
            "pending": "program_not_approved",
            "revision": "program_not_approved",
            "source": "program_source_unsupported",
        }[case]
        assert result.json()["detail"]["code"] == expected
        assert manager.evaluations.store.records == {}
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
