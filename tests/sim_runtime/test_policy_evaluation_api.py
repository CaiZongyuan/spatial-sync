"""Actual single-slot policy evaluation and teacher comparison over the public HTTP API."""

import asyncio
import json
import time
from copy import deepcopy

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.library_validation import program_schemas
from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.protocol.schema import SessionOptions
from tests.fixtures.controller_promotion_evidence import controlled_qualification
from tests.fixtures.policy_evaluation import finger_probe_inputs, native_suite
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN
from tests.unit.test_controller_publication import ControlledPublicationValidator


async def evaluate(client, suite, subject, *, expected="completed"):
    submitted = await client.post("/api/v1/evaluations", json={"subject": subject, "suite": suite.id})
    assert submitted.status_code == 202, submitted.text
    identifier = submitted.json()["evaluationId"]
    deadline = time.monotonic() + 60
    while True:
        read = await client.get("/api/v1/evaluations/" + identifier)
        assert read.status_code == 200, read.text
        value = read.json()
        if value["status"] in ("completed", "failed", "cancelled"):
            assert value["status"] == expected, value
            return value
        assert time.monotonic() < deadline, value
        await asyncio.sleep(0.05)


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
        other_task = deepcopy(task)
        other_task["success"]["lte"] = 0.065
        other_job = uploaded_job(seed, tmp_path / "next-task", spec=spec, task_document=other_task)
        other_response = seed.post(
            "/api/v1/policies",
            json={
                "checkpoint": other_job["jobId"] + "/checkpoints/model_0.pt",
            },
        )
        assert other_response.status_code == 201, other_response.text
        other_policy = other_response.json()
        assert other_policy["taskContentHash"] != policy["taskContentHash"]
        manager = seed.app.state.manager
        scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene, timeout=45.0)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:

            teacher = await evaluate(client, suite, "robot_arm.newton")
            candidate = await evaluate(client, suite, policy["id"])
            other_candidate = await evaluate(client, suite, other_policy["id"])
            assert teacher["result"]["successes"] == 1
            assert candidate["result"]["successes"] == 0
            assert candidate["result"]["decision"] == "rejected"
            assert candidate["result"]["reference"] == teacher["evaluationId"]
            assert candidate["result"]["conditionsGroup"] == teacher["result"]["conditionsGroup"]
            assert other_candidate["result"]["conditionsGroup"] == teacher["result"]["conditionsGroup"]
            assert other_candidate["result"]["reference"] == teacher["evaluationId"]
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


async def test_known_policy_keeps_completed_and_partial_evidence_when_comparison_freeze_fails(
    tmp_path, monkeypatch, record_property
):
    settings = Settings(node_token=TOKEN)
    with TestClient(create_app(settings)) as seed:
        spec, task = finger_probe_inputs()
        job = uploaded_job(seed, tmp_path, spec=spec, task_document=task)
        created = seed.post(
            "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
        )
        assert created.status_code == 201, created.text
        policy = created.json()
        manager = seed.app.state.manager
        scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        service = app.state.manager.evaluations

        def unavailable(*args, **kwargs):
            raise OSError("Controlled missing comparison snapshot; candidate source remains available")

        monkeypatch.setattr(service, "identity_producer", unavailable)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
            completed = [await evaluate(client, suite, policy["id"]) for _ in range(2)]
            for value in completed:
                assert value["result"]["decision"] == "reported"
                assert value["result"]["reference"] is None
                assert value["result"]["comparisonState"] == "unknown"
                assert value["result"]["conditionsGroup"] == value["evaluationId"]
                assert value["episodes"][0]["policyExecution"]["inferenceCalls"] > 0
            execute = service._episode

            async def fail_after_actual_action(*args, **kwargs):
                await execute(*args, **kwargs)
                raise RuntimeError("Controlled interruption after actual Newton policy execution")

            monkeypatch.setattr(service, "_episode", fail_after_actual_action)
            partial = await evaluate(client, suite, policy["id"], expected="failed")
            attempt = partial["episodes"][0]
            assert attempt["status"] == "invalid" and "success" not in attempt
            assert attempt["cleanupComplete"] is True
            recording = app.state.manager.recordings.resolve(attempt["recordingId"], "recording.json")
            assert json.loads(recording.read_bytes())["status"] == "complete"
            assert "result" not in partial
            values = completed + [partial]
            for value in values:
                raw = service.store.records[value["evaluationId"]]
                assert raw["identitySnapshot"] is None and raw["comparisonIdentity"] is None
                assert raw["comparisonState"] == "unknown"
                assert raw["candidateIdentity"]["id"] == policy["id"]
                assert raw["candidateIdentity"] == raw["policySubject"]["candidateIdentity"]
            read = (await client.get("/api/v1/policies/training-probe/1")).json()
            assert {item["evaluationId"] for item in read["evidence"]["evaluations"]} == {
                item["evaluationId"] for item in values
            }
            assert service.store.reference(service.store.records[completed[0]["evaluationId"]]["key"]) is None
            assert all(not session.holds_resources for session in app.state.manager.sessions.values())
            record_property("unknown_comparison_policy_evaluations", json.dumps(values, sort_keys=True))


async def test_actual_newton_private_scopes_keep_train_reference_and_recordings_private(
    tmp_path, record_property
):
    settings = Settings(node_token=TOKEN)
    with TestClient(create_app(settings)) as seed:
        spec, task = finger_probe_inputs()
        job = uploaded_job(seed, tmp_path, spec=spec, task_document=task)
        created = seed.post("/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"})
        assert created.status_code == 201, created.text
        policy = created.json()
        manager = seed.app.state.manager
        scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene, name="promotion-native-private", timeout=5.0, heldout=True)
        grading = deepcopy(suite.task)
        goal = {"property": "gripperWidth", "lte": 0.06}
        grading.update(success=goal, milestones=[{"id": "closed", "when": goal}])
        (suite.path / "task.yaml").write_text(yaml.safe_dump(grading), encoding="utf-8")
    # Actual CPU Sources/actions/grades. Only the separately unmet native-training
    # qualification gate is replaced by this explicitly controlled constructor adapter.
    app = create_app(settings, controller_promotion_qualification=controlled_qualification,
                     controller_publication_validator=ControlledPublicationValidator(
                         tmp_path / "controlled-publication-validation", program_schemas
                     ))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
            teacher = await evaluate(client, suite, "robot_arm.newton")
            candidate = await evaluate(client, suite, policy["id"])
            assert teacher["result"]["successes"] == 0, teacher
            assert candidate["result"]["decision"] == "accepted", candidate
            response = await client.post("/api/v1/controller-promotions", json={
                "subject": policy["id"], "scene": scene.id, "requestId": "actual-newton-private-scopes",
            })
            assert response.status_code == 202, response.text
            promotion = response.json()
            route = "/api/v1/controller-promotions/" + promotion["id"]
            deadline = time.monotonic() + 60
            while True:
                progress = (await client.get(route)).json()
                if progress["status"] != "testing":
                    assert progress["status"] == "awaiting_human", progress
                    break
                assert time.monotonic() < deadline, progress
                await asyncio.sleep(0.05)
            service = app.state.controller_promotions
            frozen = service.get(promotion["id"])
            store = app.state.manager.evaluations.store
            assert store.reference(store.records[candidate["evaluationId"]]["key"])["evaluationId"] == (
                candidate["evaluationId"])
            recordings = []
            for side, identity in frozen["runs"].items():
                record = store.records[identity]
                assert record["result"]["decision"] == "test" and record["result"]["split"] == "test"
                assert record["observedSource"]["implementation"].endswith("NewtonSource")
                assert (await client.get("/api/v1/evaluations/" + identity)).status_code == 404
                for episode in record["episodes"]:
                    assert episode["cleanupComplete"] and episode["status"] == "valid"
                    recordings.append(episode["recordingId"])
                    recording = await client.get("/recordings/" + episode["recordingId"] + "/recording.json")
                    assert recording.status_code == 404
            review = (await client.get(route + "/review")).json()
            assert review["qualification"]["evidenceMode"] == "controlled"
            assert set(review["test"]) == {"reference", "candidate"}
            assert all(not session.holds_resources for session in app.state.manager.sessions.values())
            old = await app.state.manager.create(
                None, "newton", SessionOptions(continueWithoutObservers=True), scene_version=scene.id
            )
            try:
                published = await client.post("/api/v1/approvals/" + review["approvalId"] + "/decision",
                                              json={"decision": "approve"})
                assert published.status_code == 200, published.text
                assert published.json()["status"] == "published", published.text
                robot = (await client.get("/api/v1/embodiments/fr3-sim")).json()
                assert robot["currentController"]["scene"] != scene.id
                [active] = robot["activeSessions"]
                assert active["sessionId"] == old.session_id and active["scene"] == scene.id
                assert active["controller"] is None and old.deployment_permit is None
                assert old.scene_version.id == scene.id and old.state == "RUNNING"
            finally:
                await old.close()
            assert not old.holds_resources
            record_property("actual_newton_private_scopes", json.dumps({
                "promotion": promotion["id"], "runs": frozen["runs"], "recordings": recordings,
                "qualification": "controlled-constructor; no actual native trainer proof",
            }, sort_keys=True))
