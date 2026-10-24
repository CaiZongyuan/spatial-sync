"""Actual role-bound exposure revokes active work and restores files, including restart conflicts."""

import io
import tarfile

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.agents import pack_harness
from tests.gateway.test_agent_promotions import DECLARATION, MARKER, capture, private_suite_archive
from tests.gateway.test_agent_rsi import agent_plan, candidate_archive, complete_scope, controlled_identity
from tests.gateway.test_external_agent_evaluation import BASE, create, work


def active_round(http, app):
    suite = http.post(
        "/api/v1/suites", content=private_suite_archive(), headers={"Content-Type": "application/x-tar"}
    ).json()
    run, _ = create(http, suite=suite["id"], purpose="train", caseId=None, runtime=DECLARATION)
    complete_scope(http, run)
    app.state.agent_rsi.identities = controlled_identity
    baseline = http.post(
        "/api/v1/agents/lab-operator/baseline",
        json={"evaluationId": run["id"], "requestId": "contamination-baseline"},
    ).json()
    original = http.get("/api/v1/agents/lab-operator/versions/1/snapshot").content
    candidate_bytes = candidate_archive(original)
    candidate = http.post(
        "/api/v1/agents/lab-operator/snapshots",
        content=candidate_bytes,
        headers={"Content-Type": "application/x-tar"},
    ).json()
    optimizer = http.post(
        "/api/v1/agents/lab-operator/optimizer-grant",
        json={
            "referenceEvaluationId": run["id"],
            "goalId": "goal/rsi-fixture",
            "nativeSessionId": "controlled-exposed-optimizer",
            "requestId": "exposure-optimizer",
            "wallSeconds": 120,
        },
    ).json()
    optimizer = {"Authorization": "Bearer " + optimizer["token"], "X-Actor": "user:forged"}
    payload = agent_plan(baseline, candidate) | {"runtime": DECLARATION}
    plan = http.post("/api/v1/plans", headers=optimizer, json=payload)
    assert plan.status_code == 201, plan.text
    plan = plan.json()
    assert (
        http.post(
            "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
        ).status_code
        == 200
    )
    run = http.post(
        "/api/v1/plans/" + plan["id"] + "/agent-runs",
        headers=optimizer,
        json={"revision": 1, "requestId": "exposed-round"},
    )
    assert run.status_code == 202, run.text
    run = run.json()
    item = work(http, run)
    return plan, run, item, optimizer, original, candidate_bytes


@pytest.mark.parametrize(
    "exposure",
    ["workflow-input", "workflow-publication-interruption", "controller-event-with-editor-conflict"],
)
def test_exposure_stops_owned_source_preserves_evidence_and_restores_reference(exposure):
    settings = Settings(
        remote=True, operator_token="owned-exposure-controller", remote_requests_per_minute=10000
    )
    operator = {"Authorization": "Bearer " + settings.operator_token}
    app = create_app(settings)
    with TestClient(app) as http:
        http.headers.update(operator)
        plan, run, item, optimizer, original, candidate_bytes = active_round(http, app)
        base = "/api/v1/plans/" + plan["id"]
        directory = app.state.agents.user_root / "agents/lab-operator/harness"
        denied = http.get("/api/v1/inbox", headers=optimizer)
        assert denied.status_code == 403
        assert http.get(base).json()["status"] == "evaluating"  # A denial alone is not exposure.
        if exposure.startswith("workflow"):
            files = http.get("/api/v1/agents/lab-operator/harness/1", headers=optimizer).json()["files"]
            suite = app.state.agent_rsi.manager.evaluations.suites.resolve(plan["suite"])
            private_statement = suite.statements["held-out-safe-sensor"]
            next(file for file in files if file["path"] == "AGENTS.md")["content"] += "\n" + private_statement
            if exposure == "workflow-publication-interruption":
                from packages.info.plans import PlanError

                save = app.state.agent_rsi._save

                def interrupted(record):
                    if record.context.get("contamination", {}).get("application") == "complete":
                        raise PlanError(
                            "agent_rsi_unavailable", "Controlled stop publication failure", status=503
                        )
                    return save(record)

                app.state.agent_rsi._save = interrupted
            response = http.post(
                "/api/v1/agents/lab-operator/candidates",
                headers=optimizer,
                json={
                    "referenceVersion": 1,
                    "requestId": "exposed-input",
                    "files": files,
                    "change": plan["change"],
                },
            )
            if exposure == "workflow-publication-interruption":
                assert response.status_code == 503, response.text
                assert http.get(base).json()["stop"]["application"] == "stopping"
            else:
                assert response.status_code == 409
                assert response.json()["detail"]["code"] == "agent_workflow_contaminated"
            assert MARKER not in response.text
            assert len(list((settings.state_root / "agent-rsi-exposures").glob("*.json"))) == 1
        else:
            imported = http.post(
                "/api/v1/runtime-sessions/import", json=capture("controlled-exposed-optimizer")
            )
            assert imported.status_code == 202, imported.text
            identity = imported.json()["session"]["id"]
            event = http.get("/api/v1/runtime-sessions/" + identity).json()["events"][1]
            body = {
                "revision": 1,
                "requestId": "controller-exposure",
                "runtimeRecordId": identity,
                "eventId": event["id"],
                "eventHash": event["contentHash"],
            }
            rejected = http.post(base + "/contamination", headers=optimizer, json=body)
            assert rejected.status_code == 409, (
                rejected.text
            )  # Canary alone is not a full known-private input.
            (directory / "AGENTS.md").write_text("Preserve the person's concurrent edit.\n", encoding="utf-8")
            response = http.post(base + "/contamination", json=body)
            assert response.status_code == 200, response.text
            assert response.json()["stop"]["application"] == "restore_blocked"
            assert (directory / "AGENTS.md").read_text(
                encoding="utf-8"
            ) == "Preserve the person's concurrent edit.\n"
            assert http.get("/api/v1/runtime-sessions/" + identity).status_code == 404
            assert (
                http.get("/api/v1/runtime-sessions/" + identity + "/artifacts/transcript").status_code == 404
            )
            costs = http.get("/api/v1/cost/summary", params={"range": "all", "goal": plan["goalId"]}).json()
            assert costs["totals"]["llmUsd"] == 0.3244434
            late = capture("controlled-exposed-optimizer")
            late["records"][-1]["total_cost_usd"] = 0.5
            late["records"][-1]["modelUsage"]["claude-opus-5-5"]["costUSD"] = 0.5
            refreshed = http.post(base + "/contamination/runtime-records/import", json=late)
            assert refreshed.status_code == 202 and refreshed.json()["runtimeRecordId"] == identity, (
                refreshed.text
            )
            costs = http.get("/api/v1/cost/summary", params={"range": "all", "goal": plan["goalId"]}).json()
            assert costs["totals"]["llmUsd"] == 0.5
            assert http.get("/api/v1/runtime-sessions/" + identity).status_code == 404
        saved = http.get(base)
        assert saved.json()["status"] == "contaminated" and MARKER not in saved.text
        assert http.get(BASE + run["id"]).json()["cleanup"] == {"complete": True, "blocked": False}
        assert http.get("/api/v1/sessions/" + item["sessionId"]).json()["state"] == "STOPPED"
        target = {"Authorization": "Bearer " + item["connection"]["token"]}
        assert http.get("/api/v1/sessions/" + item["sessionId"] + "/state", headers=target).status_code == 403
        assert http.get(base, headers=optimizer).status_code == 403
        assert (
            http.post(base + "/agent-runs", json={"revision": 1, "requestId": "exposed-round"}).status_code
            == 409
        )
        assert http.post(base + "/settle", json={"revision": 1}).status_code == 409
        assert (
            http.post(
                BASE + run["id"] + "/complete",
                json={"caseRunId": item["caseRunId"], "nativeSessionId": "late-completion"},
            ).status_code
            == 409
        )
        if exposure.startswith("workflow"):
            assert pack_harness(directory) == original
            entries = http.get("/api/v1/notebooks/" + plan["goalId"]).json()["entries"]
            if exposure == "workflow-input":
                assert entries[0]["reportedDecision"] == "contaminated" and entries[0]["score"] is None
            else:
                assert entries == []
        else:
            assert (
                http.post(base + "/contamination", json=body).json()["stop"]["application"]
                == "restore_blocked"
            )
    with TestClient(create_app(settings)) as http:
        http.headers.update(operator)
        saved = http.get(base).json()
        if not exposure.startswith("workflow"):
            assert saved["stop"]["application"] == "restore_blocked"
            assert (directory / "AGENTS.md").read_text(
                encoding="utf-8"
            ) == "Preserve the person's concurrent edit.\n"
            # Explicit human fixture resolution; restart reuses the original transition intent.
            with tarfile.open(fileobj=io.BytesIO(candidate_bytes)) as tar:
                (directory / "AGENTS.md").write_bytes(tar.extractfile("AGENTS.md").read())
            response = http.post(base + "/contamination", json=body)
            assert response.status_code == 200 and response.json()["stop"]["application"] == "complete", (
                response.text
            )
        assert pack_harness(directory) == original
        assert http.get("/api/v1/agents/lab-operator").json()["currentVersion"] == 1
        assert http.get("/api/v1/agents/lab-operator/versions/2/snapshot").content == candidate_bytes
        assert http.get(base).json()["status"] == "contaminated"
        assert http.get(base).json()["stop"]["application"] == "complete"
        for path in (
            base,
            "/api/v1/notebooks/" + plan["goalId"],
            "/api/v1/inbox",
            "/api/v1/runtime-sessions",
        ):
            assert MARKER not in http.get(path).text
        assert http.get(base, headers=optimizer).status_code == 401
