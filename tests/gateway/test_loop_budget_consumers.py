"""Real legacy Task evidence and Job writers distinguish authority from derived allowance."""

from copy import deepcopy

import pytest
import yaml
from fastapi.testclient import TestClient
from test_training_jobs import HEARTBEAT, NODE, TOKEN, approve, heartbeat, setup_plan

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import LibraryError
from tests.fixtures.training_task import task_archive, task_directory, training_plan


def legacy_shape(manager, plan_id):
    record = manager.training_plans.get(plan_id)
    plan = record.plan.model_copy(update={"goalId": None})
    manager.training_plans._save(record.model_copy(update={"plan": plan, "goalContext": None}))
    approval = manager.approvals.records[record.approvalId]
    details = deepcopy(approval.details)
    details.pop("loopBudget", None)
    details["plan"].pop("goalId", None)
    manager.approvals._save(approval.model_copy(update={"details": details}))


@pytest.mark.parametrize("old_task_available", [False, True])
def test_earliest_legacy_approval_never_borrows_the_later_task_999_budget(
    tmp_path, monkeypatch, old_task_available
):
    settings = Settings(node_token=TOKEN)
    app = create_app(settings)
    with TestClient(app) as client:
        first, task = setup_plan(client)
        approve(client, first["approvalId"])
        legacy_shape(app.state.manager, first["id"])
    # Reload real durable pre-feature shape, rather than creating another new snapshot.
    app = create_app(settings)
    with TestClient(app) as client:
        package = task_directory(tmp_path / "next-task")
        path = package / "task.yaml"
        metadata = yaml.safe_load(path.read_text())
        metadata["loopBudget"] = {"gpuHours": 999, "episodes": 9999, "days": 999}
        path.write_text(yaml.safe_dump(metadata))
        second_task = client.post(
            "/api/v1/tasks", content=task_archive(package), headers={"Content-Type": "application/x-tar"}
        ).json()
        assert second_task["id"].endswith("@2")
        if not old_task_available:
            show = app.state.manager.tasks.show

            def missing(identifier):
                if identifier == task["id"]:
                    raise LibraryError("task_unavailable", "Owned missing old Task fixture", 409)
                return show(identifier)

            monkeypatch.setattr(app.state.manager.tasks, "show", missing)
        proposed = client.post("/api/v1/plans", json=training_plan(second_task["id"])).json()
        limit = proposed["loopBudget"]["limit"]
        assert limit["gpuHours"] == 4 if old_task_available else limit is None
        approve(client, proposed["approvalId"])
        current = app.state.manager.loop_budgets.summary("goal/training-probe")
        assert current["limit"]["gpuHours"] == 4 if old_task_available else current["limit"] is None
        assert current["limitSource"] == ("legacy-immutable-task" if old_task_available else "unknown")


@pytest.mark.parametrize("iteration_seconds,expected_after_approval", [(360.0, 10), (2880.0, 16)])
def test_two_seed_job_allowances_are_not_two_independent_goal_reservations(
    iteration_seconds, expected_after_approval
):
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        plan, task = setup_plan(client, budget=10)
        approve(client, plan["approvalId"])
        report = heartbeat(plan, task)
        # max_iterations10: estimates1h or8h; no physics/GPU process is started.
        report["profiles"][0]["iterationSeconds"] = iteration_seconds
        assert client.post(HEARTBEAT, headers=NODE, json=report).status_code == 200
        first = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        second = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 43}).json()
        assert app.state.manager.loop_budgets.summary(plan["goalId"])["effective"]["gpuHours"] == 10
        if second["approvalId"]:
            approve(client, second["approvalId"])
        else:
            assert first["allowedGpuHours"] == 10 and second["allowedGpuHours"] == 9
        assert (
            app.state.manager.loop_budgets.summary(plan["goalId"])["effective"]["gpuHours"]
            == expected_after_approval
        )
