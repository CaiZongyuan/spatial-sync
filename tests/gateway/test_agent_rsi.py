"""Agent intent uses real Synthetic grades, exact Harness bytes and human approvals."""

import io
import tarfile
import time
from copy import deepcopy

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import BUILTIN_ROOT
from tests.gateway.test_external_agent_evaluation import BASE, DECLARATION, create, work


def suite_archive():
    root = BUILTIN_ROOT / "suites/lab-ops-basic/1"
    metadata = yaml.safe_load((root / "suite.yaml").read_text(encoding="utf-8"))
    metadata.update(id="suite/rsi-fixture", title="Controlled RSI scope; actual Synthetic evidence")
    metadata["cases"] = [
        case for case in metadata["cases"] if case["id"] == "close-lid-safe" or case["split"] == "test"
    ]
    files = {"suite.yaml": yaml.safe_dump(metadata).encode()}
    for case in metadata["cases"]:
        for key in ("statement", "grading"):
            files[case[key]] = (root / case[key]).read_bytes()
    return archive(files)


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        for name, content in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))
    return output.getvalue()


def candidate_archive(original):
    with tarfile.open(fileobj=io.BytesIO(original)) as tar:
        files = {name: tar.extractfile(name).read() for name in tar.getnames()}
    files["AGENTS.md"] += b"\n\nCheck the final platform observation before reporting completion.\n"
    return archive(files)


def complete_scope(http, run):
    for _ in range(3):
        item = work(http, run)
        result = http.post(
            BASE + run["id"] + "/complete",
            json={
                "caseRunId": item["caseRunId"],
                "nativeSessionId": "scripted-" + item["caseRunId"],
            },
        )
        assert result.status_code == 200, result.text
        assert result.json()["case"]["status"] == "completed", result.text
    for _ in range(200):
        result = http.get(BASE + run["id"]).json()
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    raise AssertionError("Owned three-case train scope did not finalize")


def agent_plan(baseline, candidate):
    return {
        "kind": "training",
        "layer": "agent",
        "title": "Verify final-state guidance",
        "goal": "Improve the fixed public score",
        "goalId": "goal/rsi-fixture",
        "agentId": "agent/lab-operator",
        "suite": baseline["suite"],
        "runtime": DECLARATION,
        "referenceEvaluationId": baseline["evaluationId"],
        "referenceVersion": 1,
        "candidateVersion": candidate["version"],
        "change": {
            "kind": "instructions",
            "path": "AGENTS.md",
            "description": "Add one final-observation paragraph",
        },
        "hypotheses": [
            {
                "id": "H1",
                "cause": "Completion is reported too early",
                "change": "Check final state",
                "predict": {"metric": "score", "to": ">40"},
            }
        ],
        "notebookRead": [],
        "rationale": "One bounded change under the same conditions",
        "interview": "Target: increase mean. Recommended: final observation. Answer: agreed.",
        "budget": {"cases": 6, "wallSeconds": 60, "llmUsd": None},
        "goalBudget": {"cases": 18, "days": 1.0, "llmUsd": None},
    }


def controlled_identity(run):
    # Explicit app-owned identity producer for the R3 workflow contract only.
    # It does not claim #132 real code fingerprint validation; default production is fail-closed.
    return {
        "conditions": deepcopy(run["key"]),
        "implementation": {"environment": "controlled-environment", "judge": "controlled-judge"},
    }


def test_real_suite_three_recordings_baseline_fail_closed_and_agent_plan_survives_restart():
    settings = Settings()
    app = create_app(settings)
    with TestClient(app) as http:
        pushed = http.post(
            "/api/v1/suites", content=suite_archive(), headers={"Content-Type": "application/x-tar"}
        )
        assert pushed.status_code == 201, pushed.text
        suite = pushed.json()
        assert suite["id"] == "suite/rsi-fixture@1" and len(suite["cases"]) == 1
        assert "held-out-safe-sensor" not in pushed.text and "dimensions" not in pushed.text
        assert http.post(
            "/api/v1/suites", content=suite_archive(), headers={"Content-Type": "application/x-tar"}
        ).json()["idempotent"]
        run, _ = create(http, suite=suite["id"], purpose="train", caseId=None)
        completed = complete_scope(http, run)
        assert completed["status"] == "completed" and completed["result"]["episodes"] == 3
        request = {"evaluationId": run["id"], "requestId": "formal-baseline"}
        blocked = http.post("/api/v1/agents/lab-operator/baseline", json=request)
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "not_comparable"
        assert http.get("/api/v1/agents/lab-operator/rsi").json()["baselines"] == []
        app.state.agent_rsi.identities = controlled_identity
        response = http.post("/api/v1/agents/lab-operator/baseline", json=request)
        assert response.status_code == 200, response.text
        baseline = response.json()
        assert http.post("/api/v1/agents/lab-operator/baseline", json=request).json() == baseline
        original = http.get("/api/v1/agents/lab-operator/versions/1/snapshot").content
        candidate = http.post(
            "/api/v1/agents/lab-operator/snapshots",
            content=candidate_archive(original),
            headers={"Content-Type": "application/x-tar"},
        ).json()
        payload = agent_plan(baseline, candidate)
        response = http.post("/api/v1/plans", json=payload)
        assert response.status_code == 201, response.text
        plan = response.json()
        assert plan["layer"] == "agent" and plan["status"] == "submitted"
        assert not {"task", "jobs", "algorithm", "hyperparameters", "gpuHours"} & plan.keys()
        assert any(value["id"] == plan["id"] for value in http.get("/api/v1/plans").json()["plans"])
        assert http.get("/api/v1/health").json()["sessionCount"] == 3
    restarted_app = create_app(settings)
    with TestClient(restarted_app) as http:
        restarted_app.state.agent_rsi.identities = controlled_identity
        saved = http.get("/api/v1/plans/" + plan["id"]).json()
        assert saved["status"] == "submitted" and saved["context"] == plan["context"]
        decision = "/api/v1/approvals/" + plan["approvalId"] + "/decision"
        assert (
            http.post(
                decision, json={"decision": "approve"}, headers={"X-Actor": "agent:optimizer"}
            ).status_code
            == 403
        )
        assert http.post(decision, json={"decision": "approve"}).status_code == 200
        assert http.get("/api/v1/plans/" + plan["id"]).json()["status"] == "approved"
        assert (
            http.post(
                "/api/v1/plans/" + plan["id"] + "/revisions", json=payload | {"layer": "embodied"}
            ).status_code
            == 422
        )
