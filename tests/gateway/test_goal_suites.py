from concurrent.futures import ThreadPoolExecutor

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.library.common import BUILTIN_ROOT, document
from packages.training.inputs import pack_package
from tests.fixtures.training_task import training_plan


def test_goal_suite_freezes_at_human_approval_and_reuses_identity(tmp_path):
    original = BUILTIN_ROOT / "tasks/microduck-stand-up/1/package"
    task = document(original / "task.yaml")
    task.update(
        name="goal-suite-probe",
        declarative={
            "baseBody": "/World/Lab/Microduck/trunk_base",
            "episodeSeconds": 3.0,
            "rewards": [{"kind": "upright", "weight": 1.0}],
        },
    )
    spec = document(original / "obs_spec.yaml")
    spec["schema"] = "lw.obs-spec/2"
    package = tmp_path / "task"
    package.mkdir()
    (package / "obs_spec.yaml").write_text(yaml.safe_dump(spec))
    plan = training_plan(
        goalId="goal/suite-probe",
        evaluation={"scene": task["scene"], "episodes": 36, "threshold": 34},
    )

    def submit(client, *, parent=None, evaluation=None):
        (package / "task.yaml").write_text(yaml.safe_dump(task))
        pushed = client.post(
            "/api/v1/tasks", content=pack_package(package), headers={"Content-Type": "application/x-tar"},
        )
        assert pushed.status_code == 201, pushed.text
        route = f"/api/v1/plans/{parent}/revisions" if parent else "/api/v1/plans"
        return client.post(route, json=plan | {
            "task": pushed.json()["id"], "evaluation": evaluation or plan["evaluation"],
        })

    with TestClient(create_app()) as client:
        first = submit(client)
        assert first.status_code == 201, first.text
        first = first.json()
        preview = first["goalSuite"]
        assert preview["episodes"] == 36
        assert preview["asset"]["id"] == "robot/microduck@1"
        available = client.get("/api/v1/suites").json()["suites"]
        assert not any(row.get("goalId") == plan["goalId"] for row in available)
        route = f"/api/v1/approvals/{first['approvalId']}"
        assert client.get(route).json()["details"]["goalSuite"] == preview
        assert client.post(route + "/decision", json={"decision": "approve"},
                           headers={"X-Actor": "agent:suite-probe"}).status_code == 403
        with ThreadPoolExecutor(max_workers=2) as approvals:
            decisions = list(approvals.map(
                lambda _: client.post(route + "/decision", json={"decision": "approve"}), range(2)
            ))
        approved, retry = decisions
        assert approved.status_code == retry.status_code == 200, approved.text
        frozen = approved.json()["details"]["goalSuite"]
        assert frozen["contentHash"]
        assert retry.json()["details"]["goalSuite"] == frozen
        task["declarative"]["rewards"][0]["weight"] = 2.0
        revised = submit(client, parent=first["id"], evaluation={"scene": task["scene"], "threshold": 34})
        assert revised.status_code == 201, revised.text
        revision = revised.json()
        assert revision["goalSuite"] == frozen
        assert revision["evaluation"]["episodes"] == 36 and revision["budget"]["episodes"] == 72
        changed_count = submit(client, evaluation=plan["evaluation"] | {"episodes": 40})
        assert changed_count.status_code == 409
        assert changed_count.json()["detail"]["code"] == "goal_suite_changed"
        task["success"] = {"all": [{"property": "baseHeight", "gte": 0.2}]}
        rejected = submit(client)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "goal_suite_changed"

    with TestClient(create_app()) as client:
        name = frozen["id"].removeprefix("suite/").replace("@", "/")
        suite = client.get(f"/api/v1/suites/{name}").json()
        assert suite["contentHash"] == frozen["contentHash"] and len(suite["episodes"]) == 36
        assert set(case["id"] for case in suite["episodes"]) == {f"train-{i + 1}" for i in range(36)}
        denied = client.post("/api/v1/evaluations", json={
            "suite": frozen["id"], "subject": "policy/unreachable@1", "split": "test",
        }, headers={"X-Actor": "agent:suite-probe"})
        assert denied.status_code == 403 and denied.json()["detail"]["code"] == "test_requires_promotion"
