"""Agent intent uses real Synthetic grades, exact Harness bytes and human approvals."""

import asyncio
import io
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.agent_rsi.workflow import comparison_identity
from packages.library.common import BUILTIN_ROOT
from tests.fixtures.training_task import task_archive, training_plan
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
        # Actual provenance exists; independently prove unavailable producer facts still fail closed.
        assert app.state.agent_rsi.external.get(run["id"])["comparisonState"] == "known"
        app.state.agent_rsi.identities = lambda value: comparison_identity(
            value | {"comparisonIdentity": None}
        )
        blocked = http.post("/api/v1/agents/lab-operator/baseline", json=request)
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "not_comparable"
        assert http.get("/api/v1/agents/lab-operator/rsi").json()["baselines"] == []
        app.state.agent_rsi.identities = comparison_identity
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
        saved = http.get("/api/v1/plans/" + plan["id"]).json()
        assert saved["status"] == "submitted" and saved["context"] == plan["context"]
        decision = "/api/v1/approvals/" + plan["approvalId"] + "/decision"
        start_path = "/api/v1/plans/" + plan["id"] + "/agent-runs"
        assert http.post(start_path, json={"revision": 1, "requestId": "round-one"}).status_code == 409
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
        response = http.post(start_path, json={"revision": 1, "requestId": "round-one"})
        assert response.status_code == 202, response.text
        candidate_run = response.json()
        assert candidate_run["planId"] == plan["id"] and candidate_run["goalId"] == plan["goalId"]
        item = work(http, candidate_run)
        target = {"Authorization": "Bearer " + item["connection"]["token"]}
        collision = http.post(
            "/api/v1/plans",
            headers=target,
            json={
                "id": plan["id"],
                "title": "Attempt to shadow the parent Agent Plan",
                "sessionId": item["sessionId"],
                "target": "session",
                "steps": [{"id": 1, "do": {"entity": "/World/Lab/RobotA", "action": "home", "params": {}}}],
            },
        )
        assert collision.status_code == 409, collision.text
        assert http.get("/api/v1/plans/" + plan["id"], headers=target).status_code == 403
        final = complete_scope(http, candidate_run)
        assert final["result"]["score"] == baseline["score"]
        settled = http.post("/api/v1/plans/" + plan["id"] + "/settle", json={"revision": 1})
        assert settled.status_code == 200, settled.text
        assert settled.json()["result"]["decision"] == "rejected"
        assert settled.json()["result"]["files"]["status"] == "committed"
        assert http.get("/api/v1/agents/lab-operator").json()["currentVersion"] == 1
        from packages.library.agents import pack_harness

        directory = restarted_app.state.agents.user_root / "agents/lab-operator/harness"
        assert pack_harness(directory) == original
        assert http.get("/api/v1/agents/lab-operator/versions/2/snapshot").content != original
        notebook = http.get("/api/v1/notebooks/goal/rsi-fixture").json()["entries"]
        assert len(notebook) == 1 and notebook[0]["layer"] == "agent"
        assert notebook[0]["score"]["score"] == baseline["score"] and notebook[0]["score"]["ci95"] is None
        assert notebook[0]["cost"]["llmUsd"] is None


def test_all_plan_layers_share_atomic_namespace_for_explicit_and_generated_ids(monkeypatch):
    app = create_app()
    with TestClient(app) as http:
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
                "requestId": "namespace-baseline",
            },
        ).json()
        original = http.get("/api/v1/agents/lab-operator/versions/1/snapshot").content
        candidate = http.post(
            "/api/v1/agents/lab-operator/snapshots",
            content=candidate_archive(original),
            headers={"Content-Type": "application/x-tar"},
        ).json()
        task = http.post(
            "/api/v1/tasks", content=task_archive(), headers={"Content-Type": "application/x-tar"}
        ).json()
        session = http.post(
            "/api/v1/sessions", json={"sceneVersion": "lab-demo@3", "source": "synthetic"}
        ).json()
        identity = "plan-0123456789abcdef"
        operation = {
            "id": identity,
            "title": "Operation namespace owner",
            "sessionId": session["sessionId"],
            "steps": [{"id": 1, "do": {"entity": "/World/Lab/RobotA", "action": "home", "params": {}}}],
        }
        # These are separately valid embodied and Agent intents, sharing only the malicious new ID.
        embodied = training_plan(task["id"]) | {"id": identity}
        agent = agent_plan(baseline, candidate) | {"id": identity}
        entered, release = Event(), Event()
        publish = app.state.manager.plans.create

        async def pending_operation(*args, **kwargs):
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)
            return await publish(*args, **kwargs)

        monkeypatch.setattr(app.state.manager.plans, "create", pending_operation)
        with ThreadPoolExecutor(3) as pool:
            first = pool.submit(http.post, "/api/v1/plans", json=operation)
            assert entered.wait(5)
            competitors = [
                pool.submit(http.post, "/api/v1/plans", json=payload) for payload in (embodied, agent)
            ]
            try:
                for future in competitors:
                    with pytest.raises(TimeoutError):
                        future.result(timeout=0.1)
            finally:
                release.set()
            results = [first.result(timeout=5), *(future.result(timeout=5) for future in competitors)]
        assert [result.status_code for result in results] == [201, 409, 409], [
            result.text for result in results
        ]
        assert (
            len([plan for plan in http.get("/api/v1/plans").json()["plans"] if plan["id"] == identity]) == 1
        )
        monkeypatch.setattr(
            "apps.api.app.plan_routes.uuid.uuid4", lambda: SimpleNamespace(hex="0123456789abcdef")
        )
        generated_collision = http.post(
            "/api/v1/plans", json={key: value for key, value in operation.items() if key != "id"}
        )
        assert generated_collision.status_code == 409


@pytest.mark.parametrize("identity", [[], {}, None, 42, True])
@pytest.mark.parametrize("variant", [{}, {"kind": "training"}, {"kind": "training", "layer": "agent"}])
def test_invalid_plan_id_is_rejected_before_any_variant_or_source_effect(identity, variant):
    def no_source(*args):
        raise AssertionError("Malformed Plan identity must not create or resolve a Source")

    with TestClient(create_app(source_factory=no_source)) as http:
        response = http.post("/api/v1/plans", json=variant | {"id": identity})
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_plan"
        assert http.get("/api/v1/plans").json()["plans"] == []
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
