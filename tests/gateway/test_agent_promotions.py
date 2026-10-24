"""Actual Synthetic train acceptance, fresh private handoff and human Reference transaction."""

import io
import json
import tarfile
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.agents import pack_harness
from tests.gateway.test_agent_rsi import (
    agent_plan,
    archive,
    candidate_archive,
    complete_scope,
    suite_archive,
)
from tests.gateway.test_external_agent_evaluation import BASE, CENTRIFUGE, create, work

MARKER = "CONTROLLED-PRIVATE-RSI-CANARY-45"
DECLARATION = {"name": "claude-code", "version": "2.1.306", "model": "claude-opus-5-5", "thinking": "none"}


def private_suite_archive():
    with tarfile.open(fileobj=io.BytesIO(suite_archive())) as tar:
        files = {name: tar.extractfile(name).read() for name in tar.getnames()}
    files["statements/held-out-safe-sensor.md"] += ("\n" + MARKER + "\n").encode()
    return archive(files)


def action(http, item, entity, name, params=None):
    return http.post(
        "/api/v1/sessions/" + item["sessionId"] + "/actions",
        headers={"Authorization": "Bearer " + item["connection"]["token"]},
        json={
            "requestId": "controlled-" + name,
            "entityKey": entity,
            "action": name,
            "params": params or {},
            "wait": True,
        },
    )


def accepted_round(http, app, *, varied_scores=False):
    suite = http.post(
        "/api/v1/suites", content=private_suite_archive(), headers={"Content-Type": "application/x-tar"}
    )
    assert suite.status_code == 201 and MARKER not in suite.text, suite.text
    run, _ = create(http, suite=suite.json()["id"], purpose="train", caseId=None, runtime=DECLARATION)
    complete_scope(http, run)
    assert app.state.agent_rsi.external.get(run["id"])["comparisonState"] == "known"
    baseline_body = {"evaluationId": run["id"], "requestId": "accepted-baseline"}
    baseline = http.post("/api/v1/agents/lab-operator/baseline", json=baseline_body).json()
    actual_identity = app.state.agent_rsi.external.get(run["id"])["comparisonIdentity"]
    assert all(value not in json.dumps(baseline) for value in actual_identity.values())
    original = http.get("/api/v1/agents/lab-operator/versions/1/snapshot").content
    candidate_bytes = candidate_archive(original)
    candidate = http.post(
        "/api/v1/agents/lab-operator/snapshots",
        content=candidate_bytes,
        headers={"Content-Type": "application/x-tar"},
    ).json()
    payload = agent_plan(baseline, candidate) | {"runtime": DECLARATION}
    payload["budget"]["cases"] = 9
    plan = http.post("/api/v1/plans", json=payload).json()
    approved = http.post(
        "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    run = http.post(
        "/api/v1/plans/" + plan["id"] + "/agent-runs", json={"revision": 1, "requestId": "accepted-round"}
    )
    assert run.status_code == 202, run.text
    run = run.json()
    for repeat in range(3):
        item = work(http, run)
        if not varied_scores or repeat > 0:
            assert action(http, item, "/World/Lab/RobotA", "home").json()["status"] == "succeeded"
            assert action(http, item, CENTRIFUGE, "closeLid").json()["status"] == "succeeded"
        response = http.post(
            BASE + run["id"] + "/complete",
            json={"caseRunId": item["caseRunId"], "nativeSessionId": "accepted-" + item["caseRunId"]},
        )
        assert response.status_code == 200, response.text
    for _ in range(200):
        if http.get(BASE + run["id"]).json()["status"] == "completed":
            break
        time.sleep(0.01)
    settled = http.post("/api/v1/plans/" + plan["id"] + "/settle", json={"revision": 1})
    assert settled.status_code == 200, settled.text
    assert settled.json()["result"]["decision"] == "accepted"
    assert settled.json()["result"]["score"] > baseline["score"]
    assert http.get("/api/v1/agents/lab-operator").json()["referenceVersion"] == 1
    assert pack_harness(app.state.agents.user_root / "agents/lab-operator/harness") == candidate_bytes
    return plan, baseline_body, candidate_bytes


def private_work(http, path, headers):
    for _ in range(300):
        response = http.post(path + "/next", headers=headers)
        assert response.status_code == 200, response.text
        if response.json()["status"] in ("pending-native", "work"):
            return response.json()
        time.sleep(0.01)
    raise AssertionError("Owned private queue did not hand off work")


def capture(native_session):
    source = Path(__file__).resolve().parents[1] / "fixtures/runtime/claude-code-stream.jsonl"
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    for record in records:
        record["session_id"] = native_session
    # Explicit controlled private native input; no paid model is launched.
    records[1]["message"]["content"] = [{"type": "text", "text": MARKER}]
    return {"format": "claude-code-stream-json", "records": records}


@pytest.mark.parametrize("interrupt_publication", [False, True])
def test_accepted_round_private_native_bind_and_human_promotion_have_no_public_test_projection(
    interrupt_publication,
):
    settings = Settings(
        remote=True, operator_token="owned-promotion-controller", remote_requests_per_minute=10000
    )
    operator = {"Authorization": "Bearer " + settings.operator_token}
    app = create_app(settings)
    with TestClient(app) as http:
        http.headers.update(operator)
        plan, baseline_body, candidate_bytes = accepted_round(http, app)
        optimizer = http.post(
            "/api/v1/agents/lab-operator/optimizer-grant",
            json={
                "referenceEvaluationId": baseline_body["evaluationId"],
                "goalId": plan["goalId"],
                "nativeSessionId": "promotion-controlled-optimizer",
                "requestId": "promotion-optimizer",
                "wallSeconds": 120,
            },
        ).json()
        optimizer = {"Authorization": "Bearer " + optimizer["token"], "X-Actor": "user:forged"}
        path = "/api/v1/plans/" + plan["id"] + "/promotions"
        body = {"revision": 1, "requestId": "private-promotion"}
        response = http.post(path, headers=optimizer, json=body)
        assert response.status_code == 202, response.text
        promotion = response.json()
        assert "test" not in promotion and "runs" not in promotion
        assert http.post(path, headers=optimizer, json=body).json()["id"] == promotion["id"]
        path = "/api/v1/agent-promotions/" + promotion["id"]
        assert http.get(path + "/review", headers=optimizer).status_code == 403
        controller = http.post(path + "/controller-grant", json={"wallSeconds": 120})
        assert controller.status_code == 201, controller.text
        controller = {"Authorization": "Bearer " + controller.json()["token"]}
        assert http.get("/api/v1/agents/lab-operator", headers=controller).status_code == 403
        private_ids, native_ids = [], []
        for index in range(6):
            pending = private_work(http, path, controller)
            assert pending["status"] == "pending-native"
            opaque = pending["work"]
            assert not {"statement", "caseId", "repeat", "connection", "sessionId"} & opaque.keys()
            native = str(uuid.uuid5(uuid.NAMESPACE_URL, opaque["caseRunId"]))
            unbound = http.post(
                path + "/complete",
                headers=controller,
                json={"caseRunId": opaque["caseRunId"], "nativeSessionId": native},
            )
            assert unbound.status_code == 409, unbound.text
            if index == 0:
                reused = http.post(
                    path + "/bind-native",
                    headers=controller,
                    json={
                        "caseRunId": opaque["caseRunId"],
                        "nativeSessionId": "promotion-controlled-optimizer",
                    },
                )
                assert reused.status_code == 409
                early = http.post("/api/v1/runtime-sessions/import", json=capture(native))
                assert early.status_code == 202, early.text
                early_id = early.json()["session"]["id"]
            bound = http.post(
                path + "/bind-native",
                headers=controller,
                json={"caseRunId": opaque["caseRunId"], "nativeSessionId": native},
            )
            assert bound.status_code == 200, bound.text
            item = private_work(http, path, controller)
            assert item["status"] == "work" and MARKER in item["work"]["statement"]
            item = item["work"]
            private_ids.append(item["sessionId"])
            target = {"Authorization": "Bearer " + item["connection"]["token"]}
            assert (
                http.get("/api/v1/sessions/" + item["sessionId"] + "/state", headers=target).status_code
                == 200
            )
            assert http.get("/api/v1/sessions/" + item["sessionId"]).status_code == 404
            assert (
                http.get(
                    "/api/v1/history",
                    params={
                        "sessionId": item["sessionId"],
                        "key": CENTRIFUGE,
                        "property": "lidOpen",
                        "since": "-1s",
                    },
                ).status_code
                == 404
            )
            completion = {"caseRunId": item["caseRunId"], "nativeSessionId": native}
            if index == 0:
                assert http.get("/api/v1/runtime-sessions/" + early_id).status_code == 404
                assert http.post("/api/v1/runtime-sessions/import", json=capture(native)).status_code == 409
                imported = http.post(
                    path + "/runtime-records/import", headers=controller, json=capture(native)
                )
                assert imported.status_code == 202, imported.text
                completion["runtimeRecordId"] = imported.json()["runtimeRecordId"]
                native_ids.append(completion["runtimeRecordId"])
            if index >= 3:
                rejected = action(http, item, "/World/Lab/Incubator", "setSetpoint", {"temperatureC": 45})
                assert rejected.json()["status"] == "rejected", rejected.text
            done = http.post(path + "/complete", headers=controller, json=completion)
            assert done.status_code == 200, done.text
            assert "score" not in done.text and "dimensions" not in done.text
        for _ in range(200):
            card = http.get(path + "/review")
            if card.json()["status"] == "waiting-human":
                break
            time.sleep(0.01)
        assert card.status_code == 200, card.text
        card = card.json()
        assert card["candidateBelowReference"]
        assert card["test"]["reference"]["result"]["score"] > card["test"]["candidate"]["result"]["score"]
        assert http.get("/api/v1/agents/lab-operator").json()["referenceVersion"] == 1
        for run in card["test"].values():
            assert http.get(BASE + run["id"]).status_code == 404
            assert http.get("/api/v1/evaluations/" + run["id"]).status_code == 404
            assert http.post(BASE + run["id"] + "/next").status_code == 404
            for case in run["cases"]:
                assert http.get(case["artifacts"][0]["url"]).status_code == 200
                assert http.get(case["artifacts"][0]["url"], headers=optimizer).status_code == 403
                ordinary = BASE + run["id"] + "/cases/" + case["caseRunId"] + "/artifacts/trace"
                assert http.get(ordinary).status_code == 404
                assert (
                    http.get(
                        "/recordings/" + case["recordingId"] + "/recording.json", headers=optimizer
                    ).status_code
                    == 403
                )
        assert http.get("/api/v1/" + MARKER, headers=optimizer).status_code == 403
        trace = http.get(
            "/api/v1/agents/lab-operator/tool-trace", params={"goal": plan["goalId"]}, headers=optimizer
        )
        assert trace.status_code == 200 and MARKER not in trace.text
        assert all(identity not in trace.text for identity in private_ids + native_ids)
        public_paths = (
            "/api/v1/agent-runs",
            "/api/v1/evaluations",
            "/api/v1/health",
            "/api/v1/recordings",
            "/api/v1/approvals",
            "/api/v1/inbox",
            "/api/v1/runtime-sessions",
            "/api/v1/plans",
            "/api/v1/notebooks/" + plan["goalId"],
            "/api/v1/loops/" + plan["goalId"],
            "/api/v1/cost/summary?range=all",
            "/api/v1/cost/breakdown?range=all&by=plan",
        )
        for public_path in public_paths:
            response = http.get(public_path)
            assert response.status_code == 200, response.text
            assert MARKER not in response.text
            assert all(identity not in response.text for identity in private_ids + native_ids), public_path
        summary = http.get(
            "/api/v1/cost/summary", params={"range": "all", "goal": plan["goalId"]}, headers=optimizer
        )
        assert summary.status_code == 200 and summary.json()["totals"]["llmUsd"] == 0.3244434, summary.text
        approval_path = "/api/v1/approvals/" + card["approvalId"] + "/decision"
        assert http.post(approval_path, headers=optimizer, json={"decision": "approve"}).status_code == 403
        private_run = app.state.agent_rsi.external.get(card["test"]["reference"]["id"])
        saved_hash = private_run["identitySnapshot"]["files"]["packages/agent_rsi/workflow.py"]
        private_run["identitySnapshot"]["files"]["packages/agent_rsi/workflow.py"] = "0" * 64
        changed = http.post(approval_path, json={"decision": "approve"})
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "not_comparable"
        assert http.get("/api/v1/agents/lab-operator").json()["referenceVersion"] == 1
        private_run["identitySnapshot"]["files"]["packages/agent_rsi/workflow.py"] = saved_hash
        if interrupt_publication:
            from packages.info.plans import PlanError

            service = app.state.agent_rsi
            write = service._write

            def interrupted(**values):
                if any(item["status"] == "promoted" for item in values.get("promotions", {}).values()):
                    raise PlanError(
                        "agent_rsi_unavailable", "Controlled publication interruption", status=503
                    )
                return write(**values)

            service._write = interrupted
        promoted = http.post(
            approval_path, json={"decision": "approve", "note": "Controlled lower-test warning reviewed"}
        )
        if interrupt_publication:
            assert promoted.status_code == 503, promoted.text
            assert http.get(path).json()["status"] == "applying"
        else:
            assert promoted.status_code == 200 and promoted.json()["status"] == "promoted", promoted.text
            assert http.post(approval_path, json={"decision": "approve"}).json()["status"] == "promoted"
            assert http.post(approval_path, json={"decision": "deny"}).status_code == 409
        agent = http.get("/api/v1/agents/lab-operator").json()
        assert agent["currentVersion"] == agent["referenceVersion"] == 2
        assert pack_harness(app.state.agents.user_root / "agents/lab-operator/harness") == candidate_bytes
        assert http.get("/api/v1/agents/lab-operator/versions/2/snapshot").content == candidate_bytes
        old = http.post("/api/v1/agents/lab-operator/baseline", json=baseline_body)
        assert old.status_code == 200 and old.json()["harnessVersion"] == 1
    restarted = create_app(settings)
    with TestClient(restarted) as http:
        http.headers.update(operator)
        assert http.get("/api/v1/agents/lab-operator").json()["referenceVersion"] == 2
        assert http.get(path).json()["status"] == "promoted"
        assert http.get("/api/v1/plans/" + plan["id"]).json()["result"]["referenceAdvanced"]
        assert http.get("/api/v1/runtime-sessions/" + early_id).status_code == 404
        assert http.post(path + "/next", headers=controller).status_code == 401
