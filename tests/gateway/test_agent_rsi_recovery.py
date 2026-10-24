"""Admission checks happen before activation; finite human exceptions retain the original Goal clock."""

import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.gateway.test_agent_rsi import (
    agent_plan,
    candidate_archive,
    complete_scope,
    suite_archive,
)
from tests.gateway.test_external_agent_evaluation import create


def test_budget_preflight_finite_exception_and_historical_activation_cannot_overwrite_editor():
    app = create_app()
    with TestClient(app) as http:
        suite = http.post(
            "/api/v1/suites", content=suite_archive(), headers={"Content-Type": "application/x-tar"}
        ).json()
        run, _ = create(http, suite=suite["id"], purpose="train", caseId=None)
        complete_scope(http, run)
        assert app.state.agent_rsi.external.get(run["id"])["comparisonState"] == "known"
        baseline = http.post(
            "/api/v1/agents/lab-operator/baseline",
            json={"evaluationId": run["id"], "requestId": "preflight-baseline"},
        ).json()
        original = http.get("/api/v1/agents/lab-operator/versions/1/snapshot").content
        files = http.get("/api/v1/agents/lab-operator/harness/1").json()["files"]
        with tarfile.open(fileobj=io.BytesIO(candidate_archive(original))) as tar:
            next(file for file in files if file["path"] == "AGENTS.md")["content"] = (
                tar.extractfile("AGENTS.md").read().decode()
            )
        change = {
            "kind": "instructions",
            "path": "AGENTS.md",
            "description": "One final-observation paragraph",
        }
        candidate = http.post(
            "/api/v1/agents/lab-operator/candidates",
            json={
                "referenceVersion": 1,
                "requestId": "preflight-candidate",
                "files": files,
                "change": change,
            },
        ).json()
        payload = agent_plan(baseline, candidate)
        payload["goalBudget"]["cases"] = 2
        plan = http.post("/api/v1/plans", json=payload).json()
        assert (
            http.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        basis = http.get("/api/v1/loops/" + plan["goalId"]).json()["budget"]["basis"]
        path = "/api/v1/plans/" + plan["id"]
        blocked = http.post(path + "/agent-runs", json={"revision": 1, "requestId": "preflight-round"})
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "agent_budget_exhausted"
        assert http.get("/api/v1/agents/lab-operator").json()["currentVersion"] == 1
        assert http.get("/api/v1/health").json()["sessionCount"] == 3
        payload["budgetException"] = {
            "reason": "One finite extra case for the fixed train scope",
            "additionalCases": 1,
        }
        revised = http.post(path + "/revisions", json=payload)
        assert revised.status_code == 201, revised.text
        revised = revised.json()
        decision = "/api/v1/approvals/" + revised["approvalId"] + "/decision"
        assert http.post(decision, json={"decision": "approve"}).status_code == 409
        assert (
            http.post(
                decision, json={"decision": "approve", "note": "Approve exactly one additional case"}
            ).status_code
            == 200
        )
        current_basis = http.get("/api/v1/loops/" + plan["goalId"]).json()["budget"]["basis"]
        assert current_basis == basis
        # Controlled disconnect after real activation, before the first B dispatch.
        app.state.agent_rsi.prepare_run(plan["id"], 2, "preflight-round")
        directory = app.state.agents.user_root / "agents/lab-operator/harness"
        edited = directory / "AGENTS.md"
        edited.write_text("A later human edit after committed activation.\n", encoding="utf-8")
        blocked = http.post(path + "/agent-runs", json={"revision": 2, "requestId": "preflight-round"})
        assert (
            blocked.status_code == 409 and blocked.json()["detail"]["code"] == "harness_transition_conflict"
        )
        assert edited.read_text(encoding="utf-8") == "A later human edit after committed activation.\n"
        assert http.get("/api/v1/health").json()["sessionCount"] == 3
        edited.write_text(
            next(file["content"] for file in files if file["path"] == "AGENTS.md"),
            encoding="utf-8",
            newline="",
        )
        started = http.post(path + "/agent-runs", json={"revision": 2, "requestId": "preflight-round"})
        assert started.status_code == 202, started.text
        complete_scope(http, started.json())
        assert http.post(path + "/settle", json={"revision": 2}).json()["result"]["decision"] == "rejected"
        budget = http.get("/api/v1/loops/" + plan["goalId"]).json()["budget"]
        assert budget["limits"]["cases"] == 2 and budget["committedCases"] == 3
        assert (
            budget["basis"] == basis and budget["approvedExceptions"][0]["extension"]["additionalCases"] == 1
        )


@pytest.mark.parametrize("tail", [b'{"phase":', b'{"invalid":true}\n'])
def test_trace_partial_append_recovers_and_complete_corruption_fails_closed(tail):
    settings = Settings()
    settings.state_root.mkdir(parents=True, exist_ok=True)
    path = settings.state_root / "agent-rsi-tools.jsonl"
    row = {
        "at": "2026-10-25T00:00:00Z",
        "principal": "optimizer",
        "principalId": "retained",
        "agentId": "agent/lab-operator",
        "goalId": "goal/trace",
        "phase": "begin",
        "status": None,
    }
    valid = (json.dumps(row) + "\n").encode()
    path.write_bytes(valid + tail)
    with TestClient(create_app(settings)) as http:
        state = http.get("/api/v1/agents/lab-operator/rsi").json()
        trace = http.get("/api/v1/agents/lab-operator/tool-trace", params={"goal": "goal/trace"})
        if tail.endswith(b"\n"):
            assert not state["auditHealthy"] and trace.status_code == 503
        else:
            assert state["auditHealthy"] and trace.status_code == 200
            assert trace.json()["calls"] == [row] and path.read_bytes() == valid
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
