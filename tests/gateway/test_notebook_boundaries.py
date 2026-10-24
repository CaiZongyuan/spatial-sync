"""Required evidence and queued-save ownership close actual admission gaps."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_notebooks_api import SUITE, prepare, until

from apps.api.app.main import create_app
from apps.api.app.notebook_service import implementation_identities
from apps.api.app.settings import Settings
from tests.fixtures.training_task import training_plan


@pytest.mark.parametrize("missing", ["uv.lock", "packages/programs"])
def test_missing_required_implementation_input_never_hashes_as_a_complete_empty_tree(
    tmp_path, monkeypatch, missing
):
    roots = (
        "packages/programs",
        "packages/sim_runtime",
        "apps/api/app/sources",
        "packages/protocol",
        "packages/info",
        "devices",
    )
    for name in roots:
        path = tmp_path / name
        path.mkdir(parents=True)
        if name != missing:
            (path / "public.py").write_text("value = 1\n")
    for name in ("apps/api/app/evaluations.py", "apps/api/app/session_manager.py", "uv.lock"):
        if name != missing:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("value = 1\n")
    with pytest.raises(OSError):
        implementation_identities(root=tmp_path)


@pytest.mark.parametrize("save_fails", [False, True])
def test_pending_queue_save_protects_revoke_and_old_revision_capacity_without_holding_plan_lock(
    tmp_path, monkeypatch, save_fails
):
    app = create_app(Settings())
    entered, release = threading.Event(), threading.Event()
    with TestClient(app) as client, ThreadPoolExecutor(3) as pool:
        plan = prepare(client, tmp_path / "task")
        store = app.state.manager.evaluations.store
        save = store.save

        def blocked(record):
            if record["status"] == "queued" and record.get("planId") == plan["id"]:
                entered.set()
                assert release.wait(5)
                if save_fails:
                    raise OSError("Owned queued-save failure before admission")
            return save(record)

        monkeypatch.setattr(store, "save", blocked)
        submitting = pool.submit(
            client.post,
            "/api/v1/evaluations",
            json={
                "subject": "robot_arm.virtual",
                "suite": SUITE,
                "planId": plan["id"],
                "planRevision": 1,
            },
        )
        assert entered.wait(2)
        try:
            revoke = client.post(
                f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "revoke"}
            )
            assert revoke.status_code == 409, revoke.text
            revised = client.post(
                f"/api/v1/plans/{plan['id']}/revisions",
                json=training_plan(
                    plan["task"],
                    budget={"gpuHours": 0.0, "episodes": 1},
                    evaluation={"scene": "lab-demo@3", "episodes": 1, "threshold": 1},
                ),
            )
            assert revised.status_code == 201, revised.text
            revision = revised.json()
            assert revision["loopBudget"]["effective"]["episodes"] == 2
            assert (
                client.post(
                    f"/api/v1/approvals/{revision['approvalId']}/decision", json={"decision": "approve"}
                ).status_code
                == 200
            )
            assert app.state.manager.loop_budgets.summary(plan["goalId"])["effective"]["episodes"] == 2
        finally:
            release.set()
        if save_fails:
            with pytest.raises(OSError):
                submitting.result(timeout=3)
            assert not store.records
            budget = app.state.manager.loop_budgets.summary(plan["goalId"])
            assert budget["actual"]["episodes"] == 0 and budget["effective"]["episodes"] == 1
        else:
            response = submitting.result(timeout=3)
            assert response.status_code == 202, response.text
            identifier = response.json()["evaluationId"]
            assert response.json()["planRevision"] == 1
            assert client.post(f"/api/v1/evaluations/{identifier}/cancel").status_code == 200
            until(
                lambda: client.get(f"/api/v1/evaluations/{identifier}").json(),
                lambda value: value["status"] in ("cancelled", "completed", "failed"),
            )
