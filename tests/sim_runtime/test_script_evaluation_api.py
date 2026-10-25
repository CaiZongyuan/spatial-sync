"""Recorded real Newton execution through HTTP, with the same Suite as a finite untrained policy fixture."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.policies.subjects import fixed_bindings
from packages.training.inputs import pack_package
from tests.fixtures.embodied_evaluation import native_suite
from tests.fixtures.policy_evaluation import finger_probe_inputs
from tests.fixtures.script_program import closing_program_directory
from tests.fixtures.training_task import task_archive, task_directory, training_plan
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN
from tests.sim_runtime.test_policy_evaluation_api import evaluate


async def test_real_script_http_evaluation_recording_notebook_and_same_suite_policy_comparison(
    tmp_path, record_property
):
    settings = Settings(node_token=TOKEN)
    with TestClient(create_app(settings)) as seed:
        # Existing engineering comparator: finite authored ONNX fixture, not trained-quality evidence.
        spec, policy_task = finger_probe_inputs()
        job = uploaded_job(seed, tmp_path / "policy", spec=spec, task_document=policy_task)
        created = seed.post("/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"})
        assert created.status_code == 201, created.text
        policy = created.json()
        manager = seed.app.state.manager
        scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
        config = fixed_bindings(scene, "newton")["/World/Lab/RobotA"]["config"]
        directory = closing_program_directory(tmp_path / "script", config["fingerKeys"])
        created = seed.post(
            "/api/v1/programs", content=pack_package(directory), headers={"Content-Type": "application/x-tar"}
        )
        assert created.status_code == 201, created.text
        program = created.json()
        task = task_directory(tmp_path / "task", "script-evaluation")
        pushed = seed.post(
            "/api/v1/tasks", content=task_archive(task), headers={"Content-Type": "application/x-tar"}
        )
        assert pushed.status_code == 201, pushed.text
        body = training_plan(
            pushed.json()["id"],
            algorithm={},
            model={},
            hyperparameters={},
            jobs={"seeds": []},
            program={"id": program["id"], "contentHash": program["contentHash"]},
            budget={"gpuHours": 0, "episodes": 8},
            evaluation={"scene": scene.id, "episodes": 1, "threshold": 1},
        )
        submitted = seed.post("/api/v1/plans", json=body)
        assert submitted.status_code == 201, submitted.text
        plan = submitted.json()
        assert (
            seed.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        suite = native_suite(manager, scene, name="script-policy-engineering", timeout=45.0)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
            manager = app.state.manager
            jobs_before = set(manager.jobs.records)
            teacher = await evaluate(client, suite, "robot_arm.newton", plan=plan)
            script = await evaluate(client, suite, program["id"], plan=plan)
            comparator = await evaluate(client, suite, policy["id"])
            assert set(manager.jobs.records) == jobs_before  # Script created no training Job.
            assert script["result"]["comparisonState"] == "known"
            assert script["result"]["conditionsGroup"] == teacher["result"]["conditionsGroup"]
            assert comparator["result"]["conditionsGroup"] == teacher["result"]["conditionsGroup"]
            assert script["result"]["reference"] == teacher["evaluationId"]
            assert script["result"]["successes"] == 0  # Closing fingers does not perform tube transfer.
            episode = script["episodes"][0]
            assert episode["invocationStatus"] == "succeeded" and episode["success"] is False
            execution = episode["programExecution"]
            assert (
                execution["program"] == program["id"]
                and execution["programContentHash"] == program["contentHash"]
            )
            assert execution["actionStarts"] == 1 and execution["callbacks"]["tick"] > 0
            assert execution["callbackErrors"] == 0
            session = manager.sessions[episode["recordingId"]]
            robot = next(
                entity for entity in session.info_snapshot().entities if entity.key == "/World/Lab/RobotA"
            )
            assert robot.values["gripperWidth"] < 0.02
            recording = manager.recordings.resolve(episode["recordingId"], "recording.json")
            assert json.loads(recording.read_bytes())["status"] == "complete"
            catalog = json.loads((recording.parent / "catalog.json").read_bytes())
            assert catalog["entities"]["/World/Lab/RobotA"]["config"]["programArtifact"] == body["program"]
            notebook = (await client.get("/api/v1/notebooks/" + plan["goalId"])).json()["entries"]
            entry = next(value for value in notebook if value["evaluationId"] == script["evaluationId"])
            assert entry["candidateIdentity"]["contentHash"] == program["contentHash"]
            assert entry["partialEpisodes"][0]["programExecution"] == execution
            assert entry["comparison"]["state"] == "comparable" and entry["score"]["successes"] == 0
            assert all(not value.holds_resources for value in manager.sessions.values())
            record_property(
                "actual_script_http_evidence",
                json.dumps(
                    {
                        "teacher": teacher["evaluationId"],
                        "script": script["evaluationId"],
                        "policy": comparator["evaluationId"],
                        "suite": suite.id,
                        "scriptScore": script["result"]["score"],
                        "nativeExecution": execution,
                        "gripperWidthM": robot.values["gripperWidth"],
                        "recordingId": episode["recordingId"],
                        "candidate": script["candidateIdentity"],
                        "conditionsGroup": script["result"]["conditionsGroup"],
                        "notebookComparison": entry["comparison"],
                        "scriptCreatedJobs": 0,
                        "ownedCleanupComplete": True,
                        "policyFixtureBasis": "finite authored model, not trained-quality evidence",
                    },
                    sort_keys=True,
                ),
            )
