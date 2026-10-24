"""Actual single-slot policy evaluation and teacher comparison over the public HTTP API."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.fixtures.policy_evaluation import finger_probe_inputs, native_suite
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN


async def test_real_policy_http_evaluation_uses_fixed_suite_and_same_teacher_comparison_group(
    tmp_path, record_property
):
    settings = Settings(node_token=TOKEN)
    with TestClient(create_app(settings)) as seed:
        spec, task = finger_probe_inputs()
        job = uploaded_job(seed, tmp_path, spec=spec, task_document=task)
        response = seed.post(
            "/api/v1/policies",
            json={
                "checkpoint": job["jobId"] + "/checkpoints/model_0.pt",
            },
        )
        assert response.status_code == 201, response.text
        policy = response.json()
        manager = seed.app.state.manager
        scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene, timeout=45.0)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:

            async def evaluate(subject):
                submitted = await client.post(
                    "/api/v1/evaluations", json={"subject": subject, "suite": suite.id}
                )
                assert submitted.status_code == 202, submitted.text
                identifier = submitted.json()["evaluationId"]
                deadline = time.monotonic() + 60
                while True:
                    read = await client.get("/api/v1/evaluations/" + identifier)
                    assert read.status_code == 200, read.text
                    value = read.json()
                    if value["status"] in ("completed", "failed", "cancelled"):
                        assert value["status"] == "completed", value
                        return value
                    assert time.monotonic() < deadline, value
                    await asyncio.sleep(0.05)

            teacher = await evaluate("robot_arm.newton")
            candidate = await evaluate(policy["id"])
            assert teacher["result"]["successes"] == 1
            assert candidate["result"]["successes"] == 0
            assert candidate["result"]["decision"] == "rejected"
            assert candidate["result"]["reference"] == teacher["evaluationId"]
            assert candidate["result"]["conditionsGroup"] == teacher["result"]["conditionsGroup"]
            episode = candidate["episodes"][0]
            assert episode["invocationStatus"] == "succeeded" and episode["success"] is False
            assert episode["policyExecution"]["inferenceCalls"] >= 1
            assert episode["policyExecution"]["providers"] == ["CPUExecutionProvider"]
            raw = app.state.manager.evaluations.store.records[candidate["evaluationId"]]
            assert raw["policySubject"]["evaluationScene"]["contentHash"] == scene.content_hash
            assert raw["candidateIdentity"]["id"] == policy["id"]
            assert raw["key"]["bindingHash"] == raw["policySubject"]["baseBindings"][suite.task["robot"]]
            recording = app.state.manager.recordings.resolve(episode["recordingId"], "recording.json")
            metadata = json.loads(recording.read_bytes())
            catalog = json.loads((recording.parent / "catalog.json").read_bytes())
            assert metadata["status"] == "complete" and metadata["sceneContentHash"] == scene.content_hash
            assert catalog["entities"][suite.task["robot"]]["config"]["policy"] == policy["id"]
            read_policy = (await client.get("/api/v1/policies/training-probe/1")).json()
            assert read_policy["qualified"] is False
            assert [item["evaluationId"] for item in read_policy["evidence"]["evaluations"]] == [
                candidate["evaluationId"]
            ]
            assert all(not session.holds_resources for session in app.state.manager.sessions.values())
            record_property(
                "native_evaluation",
                json.dumps(
                    {
                        "teacher": teacher["evaluationId"],
                        "policy": candidate["evaluationId"],
                        "successes": 0,
                        "runtime": raw.get("executionFacts"),
                        "episode": episode,
                    },
                    sort_keys=True,
                ),
            )
