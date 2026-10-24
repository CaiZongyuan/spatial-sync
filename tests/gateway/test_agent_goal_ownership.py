import io
import tarfile
from copy import deepcopy

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from tests.fixtures.training_task import task_archive, training_plan
from tests.gateway.test_agent_promotions import accepted_round
from tests.gateway.test_agent_rsi import agent_plan, archive, candidate_archive, complete_scope, suite_archive
from tests.gateway.test_external_agent_evaluation import DECLARATION


def baseline_for(http, agent, suite, request):
    response = http.post(
        "/api/v1/agents/" + agent + "/runs",
        json={
            "requestId": request,
            "suite": suite,
            "harnessVersion": 1,
            "runtime": DECLARATION,
            "purpose": "train",
            "wallSeconds": 60,
        },
    )
    assert response.status_code == 202, response.text
    run = response.json()
    complete_scope(http, run)
    measured = http.post(
        "/api/v1/agents/" + agent + "/baseline",
        json={
            "evaluationId": run["id"],
            "requestId": request + "-formal",
        },
    )
    assert measured.status_code == 200, measured.text
    return measured.json()


def test_goal_layer_and_agent_owner_are_reserved_by_both_plans_and_first_optimizer_grants():
    with TestClient(create_app()) as http:
        suite = http.post(
            "/api/v1/suites", content=suite_archive(), headers={"Content-Type": "application/x-tar"}
        ).json()
        baseline = baseline_for(http, "lab-operator", suite["id"], "owner-baseline")
        original = http.get("/api/v1/agents/lab-operator/versions/1/snapshot").content
        candidate = http.post(
            "/api/v1/agents/lab-operator/snapshots",
            content=candidate_archive(original),
            headers={"Content-Type": "application/x-tar"},
        ).json()
        task = http.post(
            "/api/v1/tasks", content=task_archive(), headers={"Content-Type": "application/x-tar"}
        ).json()
        embodied = http.post("/api/v1/plans", json=training_plan(task["id"], goalId="goal/embodied-owner"))
        assert embodied.status_code == 201, embodied.text
        conflict = http.post(
            "/api/v1/plans", json=agent_plan(baseline, candidate) | {"goalId": "goal/embodied-owner"}
        )
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "goal_layer_conflict"

        def grant(agent, reference, goal, native):
            return http.post(
                "/api/v1/agents/" + agent + "/optimizer-grant",
                json={
                    "referenceEvaluationId": reference["evaluationId"],
                    "goalId": goal,
                    "nativeSessionId": native,
                    "requestId": native,
                    "wallSeconds": 60,
                },
            )

        assert grant("lab-operator", baseline, "goal/embodied-owner", "layer-conflict").status_code == 409
        agent_plan_response = http.post("/api/v1/plans", json=agent_plan(baseline, candidate))
        assert agent_plan_response.status_code == 201, agent_plan_response.text
        conflict = http.post("/api/v1/plans", json=training_plan(task["id"], goalId="goal/rsi-fixture"))
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "goal_layer_conflict"
        assert "layer" not in http.get("/api/v1/loops/goal/embodied-owner").json()
        assert http.get("/api/v1/loops/goal/rsi-fixture").json()["layer"] == "agent"

        created = http.post(
            "/api/v1/agents",
            params={"id": "owner-fork"},
            content=original,
            headers={"Content-Type": "application/x-tar"},
        )
        assert created.status_code == 201, created.text
        with tarfile.open(fileobj=io.BytesIO(suite_archive())) as tar:
            files = {name: tar.extractfile(name).read() for name in tar.getnames()}
        metadata = yaml.safe_load(files["suite.yaml"])
        metadata.update(id="suite/owner-fork", target="agent/owner-fork")
        files["suite.yaml"] = yaml.safe_dump(metadata).encode()
        fork_suite = http.post(
            "/api/v1/suites", content=archive(files), headers={"Content-Type": "application/x-tar"}
        ).json()
        fork_reference = baseline_for(http, "owner-fork", fork_suite["id"], "fork-baseline")
        fork_candidate = http.post(
            "/api/v1/agents/owner-fork/snapshots",
            content=candidate_archive(original),
            headers={"Content-Type": "application/x-tar"},
        ).json()
        assert grant("lab-operator", baseline, "goal/grant-only", "first-reservation").status_code == 201
        conflict = grant("owner-fork", fork_reference, "goal/grant-only", "different-owner")
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "goal_owner_conflict"
        fork_plan = agent_plan(fork_reference, fork_candidate) | {
            "agentId": "agent/owner-fork",
            "goalId": "goal/grant-only",
        }
        assert http.post("/api/v1/plans", json=fork_plan).status_code == 409
        assert (
            http.post("/api/v1/plans", json=training_plan(task["id"], goalId="goal/grant-only")).status_code
            == 409
        )
        assert grant("owner-fork", fork_reference, "goal/fork-owned", "fresh-fork-owner").status_code == 201
        assert http.post("/api/v1/plans", json=fork_plan | {"goalId": "goal/fork-owned"}).status_code == 201


def test_agent_gap_read_projection_preserves_a_legacy_automatic_snapshot():
    app = create_app()
    with TestClient(app) as http:
        plan, _, _ = accepted_round(http, app, varied_scores=True)
        store = app.state.agent_rsi.manager.notebooks.store
        identity, entry = next(iter(store.entries.items()))
        legacy = deepcopy(entry)
        legacy["comparison"].update(remainingGapReduction=None, gapReason="agent_mean_scale")
        store._commit(
            store.entries | {identity: legacy}
        )  # Simulate the older canonical derived-field format.
        before = store.path.read_bytes()
        for path in ("/api/v1/notebooks/" + plan["goalId"], "/api/v1/loops/" + plan["goalId"]):
            projected = http.get(path)
            assert projected.status_code == 200, projected.text
            value = projected.json().get("entries", projected.json().get("rounds"))[0]
            assert value["comparison"]["remainingGapReduction"] == pytest.approx(5 / 9)
            assert value["recordedComparison"]["gapReason"] == "agent_mean_scale"
        assert store.path.read_bytes() == before
        reference = app.state.agent_rsi.external.get(plan["referenceEvaluationId"])
        reference["identitySnapshot"]["files"]["packages/agent_rsi/workflow.py"] = "0" * 64
        workflow = http.get("/api/v1/agents/lab-operator/rsi").json()
        baseline = next(
            value for value in workflow["baselines"] if value["evaluationId"] == reference["evaluationId"]
        )
        assert baseline["comparisonState"] == "unknown" and baseline["score"] is None
        assert baseline["recordedScore"] == 40
        historical = http.get("/api/v1/plans/" + plan["id"]).json()["context"]["reference"]
        assert historical["score"] == 40
