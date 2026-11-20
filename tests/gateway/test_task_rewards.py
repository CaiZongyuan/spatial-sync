import io
import tarfile

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.training.inputs import pack_package
from tests.fixtures.reward_task import reward_source
from tests.fixtures.training_task import training_plan
from tests.gateway.test_remote_training_nodes import OPERATOR, VIEWER, remote_settings


def test_reward_weights_make_an_immutable_task_and_a_pending_plan_revision(tmp_path):
    task, observation = reward_source(tmp_path / "source")
    app = create_app(remote_settings())
    with TestClient(app) as http:
        original = http.post(
            "/api/v1/tasks", headers=OPERATOR | {"Content-Type": "application/x-tar"},
            content=pack_package(tmp_path / "source"),
        )
        assert original.status_code == 201, original.text
        source = original.json()
        task_url = "/api/v1/tasks/reward-revision-probe/1"
        rewards_url = f"{task_url}/reward-weights"
        edit = {"contentHash": source["contentHash"], "layerA": [0], "layerB": [-0.5]}
        assert http.post(rewards_url, headers=VIEWER, json=edit).status_code == 403
        denied = http.post(rewards_url, headers=OPERATOR, json=edit | {"contentHash": "0" * 64})
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "task_source_changed"
        for bad in ({"layerA": []}, {"layerB": [101]}, {"layerB": [True]}):
            assert http.post(rewards_url, headers=OPERATOR, json=edit | bad).status_code == 422
        coded = http.get("/api/v1/tasks/microduck-stand-up/1", headers=OPERATOR).json()
        denied = http.post(
            "/api/v1/tasks/microduck-stand-up/1/reward-weights", headers=OPERATOR,
            json=edit | {"contentHash": coded["contentHash"]},
        )
        assert denied.status_code == 422 and denied.json()["detail"]["code"] == "task_rewards_unsupported"

        plan = training_plan(source["id"], algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
                             evaluation={"scene": task["scene"], "episodes": 30, "threshold": 1})
        first = http.post("/api/v1/plans", headers=OPERATOR, json=plan)
        assert first.status_code == 201, first.text
        first = first.json()
        edited = http.post(rewards_url, headers=OPERATOR, json=edit)
        assert edited.status_code == 201, edited.text
        revised = edited.json()
        assert revised["id"] == "task/reward-revision-probe@2"
        assert revised["contentHash"] != source["contentHash"]
        assert http.post(rewards_url, headers=OPERATOR, json=edit).json()["id"] == revised["id"]
        retained = {key: value for key, value in source.items() if key != "idempotent"}
        assert http.get(task_url, headers=OPERATOR).json() == retained
        with tarfile.open(fileobj=io.BytesIO(app.state.manager.tasks.export(revised["id"]))) as archive:
            assert archive.extractfile(task["observation"]).read() == observation
            expected = task
            expected["declarative"]["rewards"][0]["weight"] = 0
            expected["declarative"]["layerB"]["rewards"][0]["weight"] = -0.5
            assert yaml.safe_load(archive.extractfile("task.yaml").read()) == expected

        revision_url = f"/api/v1/plans/{first['id']}/revisions?baseRevision=1"
        response = http.post(revision_url, headers=OPERATOR, json=plan | {"task": revised["id"]})
        assert response.status_code == 201, response.text
        current = response.json()
        assert current["revision"] == 2 and current["status"] == "submitted"
        assert current["task"] == revised["id"]
        assert current["approvalId"] != first["approvalId"] and "estimatedGpuHours" not in current
        stale = http.post(revision_url, headers=OPERATOR, json=plan | {"task": revised["id"]})
        assert stale.status_code == 409
        historical = http.get(f"/api/v1/plans/{first['id']}?revision=1", headers=OPERATOR).json()
        assert historical["task"] == source["id"]
        job = {"planId": current["id"], "planRevision": 2, "seed": 42}
        assert http.post("/api/v1/jobs", headers=OPERATOR, json=job).status_code == 409
        checked = http.post("/api/v1/jobs", headers=OPERATOR, json=job | {"kind": "plan_validation"})
        assert checked.status_code == 202, checked.text
        assert checked.json()["taskContentHash"] == revised["contentHash"]
