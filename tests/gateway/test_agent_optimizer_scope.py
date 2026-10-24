"""Actual granted HTTP stages files safely and cannot forge human/controller authority."""

import io
import json
import tarfile

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.agents import pack_harness
from tests.gateway.test_agent_rsi import agent_plan, complete_scope, controlled_identity, suite_archive
from tests.gateway.test_external_agent_evaluation import create


def test_fileless_staging_approval_activation_editor_conflict_and_actual_rejection():
    operator_token = "owned-rsi-controller"
    settings = Settings(remote=True, operator_token=operator_token, remote_requests_per_minute=10000)
    operator = {"Authorization": "Bearer " + operator_token}
    app = create_app(settings)
    with TestClient(app) as http:
        http.headers.update(operator)
        suite = http.post(
            "/api/v1/suites", content=suite_archive(), headers={"Content-Type": "application/x-tar"}
        ).json()
        run, _ = create(http, suite=suite["id"], purpose="train", caseId=None)
        complete_scope(http, run)
        app.state.agent_rsi.identities = controlled_identity
        baseline = http.post(
            "/api/v1/agents/lab-operator/baseline",
            json={
                "evaluationId": run["id"],
                "requestId": "scope-baseline",
            },
        ).json()
        original = http.get("/api/v1/agents/lab-operator/versions/1/snapshot").content
        issued = http.post(
            "/api/v1/agents/lab-operator/optimizer-grant",
            json={
                "referenceEvaluationId": run["id"],
                "goalId": "goal/rsi-fixture",
                "nativeSessionId": "fresh-controlled-optimizer",
                "requestId": "optimizer-once",
                "wallSeconds": 120,
            },
        )
        assert issued.status_code == 201, issued.text
        token = issued.json()["token"]
        optimizer = {"Authorization": "Bearer " + token, "X-Actor": "user:forged-human"}
        http.headers.pop("Authorization")
        assert http.get("/api/v1/agents/lab-operator/rsi").status_code == 401
        assert (
            http.get("/api/v1/agents/lab-operator/rsi", headers={"X-Actor": "user:operator"}).status_code
            == 401
        )
        files = http.get("/api/v1/agents/lab-operator/harness/1", headers=optimizer).json()["files"]
        next(item for item in files if item["path"] == "AGENTS.md")["content"] += (
            "\n\nRead the final platform state before reporting completion.\n"
        )
        proposal_body = {
            "referenceVersion": 1,
            "requestId": "fileless-one",
            "files": files,
            "change": {
                "kind": "instructions",
                "path": "AGENTS.md",
                "description": "One final-observation paragraph",
            },
        }
        proposed = http.post("/api/v1/agents/lab-operator/candidates", headers=optimizer, json=proposal_body)
        assert proposed.status_code == 201, proposed.text
        candidate = proposed.json()
        assert candidate["staged"] and not candidate["applied"] and candidate["version"] == 2
        assert (
            http.post("/api/v1/agents/lab-operator/candidates", headers=optimizer, json=proposal_body).json()[
                "version"
            ]
            == 2
        )
        directory = app.state.agents.user_root / "agents/lab-operator/harness"
        assert pack_harness(directory) == original
        assert http.get("/api/v1/agents/lab-operator", headers=optimizer).json()["currentVersion"] == 1
        plan = http.post("/api/v1/plans", headers=optimizer, json=agent_plan(baseline, candidate))
        assert plan.status_code == 201, plan.text
        plan = plan.json()
        assert plan["requestedBy"]["kind"] == "agent"
        assert plan["requestedBy"]["name"].startswith("optimizer-")
        decision = "/api/v1/approvals/" + plan["approvalId"] + "/decision"
        assert http.post(decision, headers=optimizer, json={"decision": "approve"}).status_code == 403
        for path in ("/api/v1/approvals", "/api/v1/inbox", "/api/v1/vault", "/api/v1/runtime-sessions"):
            assert http.get(path, headers=optimizer).status_code == 403
        assert (
            http.post("/api/v1/agents/lab-operator/optimizer-grant", headers=optimizer, json={}).status_code
            == 403
        )
        assert http.post(decision, headers=operator, json={"decision": "approve"}).status_code == 200
        edited = directory / "AGENTS.md"
        edited.write_text("A concurrent person's edit.\n", encoding="utf-8")
        start = "/api/v1/plans/" + plan["id"] + "/agent-runs"
        response = http.post(start, headers=optimizer, json={"revision": 1, "requestId": "scope-round"})
        assert response.status_code == 409, response.text
        assert edited.read_text(encoding="utf-8") == "A concurrent person's edit.\n"
        assert http.get("/api/v1/health").json()["sessionCount"] == 3
        # Explicit human fixture resolution; the platform did not discard the editor's bytes.
        with tarfile.open(fileobj=io.BytesIO(original)) as tar:
            edited.write_bytes(tar.extractfile("AGENTS.md").read())
        response = http.post(start, headers=optimizer, json={"revision": 1, "requestId": "scope-round"})
        assert response.status_code == 202, response.text
        assert http.get("/api/v1/agents/lab-operator", headers=optimizer).json()["currentVersion"] == 2
        http.headers.update(operator)
        complete_scope(http, response.json())
        http.headers.pop("Authorization")
        settled = http.post(
            "/api/v1/plans/" + plan["id"] + "/settle", headers=optimizer, json={"revision": 1}
        )
        assert settled.status_code == 200 and settled.json()["result"]["decision"] == "rejected", settled.text
        assert pack_harness(directory) == original
        trace = http.get(
            "/api/v1/agents/lab-operator/tool-trace", params={"goal": "goal/rsi-fixture"}, headers=optimizer
        ).json()
        assert trace["healthy"] and any(call["status"] == 403 for call in trace["calls"])
        assert operator_token not in json.dumps(trace) and token not in json.dumps(trace)
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/v1/agents/lab-operator/rsi", headers=optimizer).status_code == 401
