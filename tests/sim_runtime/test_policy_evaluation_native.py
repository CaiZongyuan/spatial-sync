"""Actual CPU policy/PhysicsPort execution and recording; deliberately fails the fixed Suite goal."""

import json

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.recordings import RecordingRepository
from apps.api.app.session_manager import Session, default_source_factory
from apps.api.app.settings import Settings
from packages.assets.registry import AssetRegistry
from packages.info.approvals import ApprovalStore
from packages.info.plans import PlanStore
from packages.library.common import content_hash
from packages.policies.subjects import resolve_policy_subject, validate_policy_execution
from packages.protocol.schema import SessionOptions
from tests.fixtures.policy import observation_spec
from tests.fixtures.policy_evaluation import native_suite
from tests.fixtures.training_task import task_document
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN


async def test_native_policy_has_measured_finite_inference_and_recording_but_no_suite_success(
    tmp_path, record_property
):
    settings = Settings(node_token=TOKEN)
    app = create_app(settings)
    with TestClient(app) as client:
        spec = observation_spec()
        finger = "/World/Lab/RobotA/fr3/fr3_finger_joint1"
        spec["observations"][0].update(keys=[finger], unit="m", scale=[1.0])
        spec["action"].update(
            jointKeys=[finger], units=["m"], scale=[-1.0], lower=[0.0], upper=[0.04], maxDelta=[0.005]
        )
        task = task_document()
        # Deliberately insufficient training goal. The fixed Suite still demands an actual tube transfer.
        task["success"] = {"property": "gripperWidth", "lte": 0.06}
        job = uploaded_job(client, tmp_path, spec=spec, task_document=task)
        created = client.post(
            "/api/v1/policies",
            json={
                "checkpoint": job["jobId"] + "/checkpoints/model_0.pt",
            },
        )
        assert created.status_code == 201, created.text
        policy = created.json()
        manager = app.state.manager
        scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene)
        subject = resolve_policy_subject(manager, policy["id"], suite, scene)
        evaluator = manager.evaluations
    approvals = ApprovalStore(tmp_path / "native-approvals.jsonl")
    approvals.startup()
    plans = PlanStore(tmp_path / "native-plans.jsonl", approvals)
    plans.startup()
    recordings = RecordingRepository(settings, AssetRegistry.from_file())
    session = Session(
        scene.name,
        "newton",
        SessionOptions(device="cpu", continueWithoutObservers=True),
        settings,
        default_source_factory,
        recordings,
        approvals,
        plans,
        scene_version=scene,
        evaluation_id="eval-native-policy",
        policy_subject=subject.document(),
    )
    try:
        await session.start()
        assert session._recording_revision_ready
        outcome = await evaluator._episode(session, suite, suite.metadata["episodes"][0])
        assert outcome["invocationStatus"] == "succeeded"
        assert outcome["success"] is False and outcome["milestones"]["place"] is False
        evidence = validate_policy_execution(
            subject, await session.source.program_execution(subject.target.entityKey)
        )
        record_property("native_policy_execution", json.dumps(evidence, sort_keys=True))
        record_property("fixed_suite_success", str(outcome["success"]))
        assert evidence["inferenceCalls"] >= 1 and evidence["validOutputs"] == evidence["inferenceCalls"]
        assert evidence["actionStarts"] == 1 and evidence["providers"] == ["CPUExecutionProvider"]
        assert session.manifest.source.kind == "newton" and session.source.stats()["device"] == "cpu"
        assert session.source.stats()["activeWeldCount"] == 0
    finally:
        await session.close()
        await plans.close()
    assert session.recording.metadata["status"] == "complete"
    record_property("recording_status", session.recording.metadata["status"])
    assert session.recording.metadata["sceneContentHash"] == scene.content_hash == content_hash(scene.path)
    catalog = json.loads((session.recording.directory / "catalog.json").read_bytes())
    recorded = catalog["entities"][subject.target.entityKey]
    assert recorded["program"] == "robot_arm.policy" and recorded["config"]["policy"] == policy["id"]
    assert policy["qualified"] is False
