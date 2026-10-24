"""Policy identity and actual Catalog binding integrity, with no engine startup."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.session_manager import Session
from apps.api.app.settings import Settings
from packages.library.common import REPO_ROOT, LibraryError
from packages.library.previews import file_hash
from packages.policies.subjects import (
    PolicySubject,
    apply_policy_catalog,
    resolve_policy_subject,
    validate_base_catalog,
    validate_policy_catalog,
    validate_policy_execution,
    validate_policy_material,
    verify_policy_subject,
)
from packages.protocol.schema import SceneManifest, SessionOptions
from packages.sim_runtime.info_catalog import load_info_catalog
from tests.fixtures.policy import observation_spec
from tests.fixtures.policy_evaluation import native_suite
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN


@pytest.fixture
def policy_client():
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def policy_inputs(tmp_path, policy_client):
    spec = observation_spec()
    spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = ["/World/Lab/RobotA/fr3/fr3_joint1"]
    job = uploaded_job(policy_client, tmp_path, spec=spec)
    created = policy_client.post(
        "/api/v1/policies",
        json={
            "checkpoint": job["jobId"] + "/checkpoints/model_0.pt",
        },
    )
    assert created.status_code == 201, created.text
    manager, policy = policy_client.app.state.manager, created.json()
    scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
    suite = native_suite(manager, scene)
    # Historical geometry fixture; current preview availability is tested separately.
    path = REPO_ROOT / "assets/scene-previews" / scene.name / str(scene.rev) / scene.content_hash / "newton"
    capture = json.loads((path / "capture.json").read_bytes())
    assert capture["sceneVersion"] == scene.id and capture["sceneContentHash"] == scene.content_hash
    assert file_hash(path / "manifest.json") == capture["files"]["manifest.json"]
    manifest = SceneManifest.model_validate_json((path / "manifest.json").read_bytes())
    catalog = load_info_catalog(manifest, scene.name, scene_package=scene)
    return manager, policy, scene, suite, catalog


def test_policy_intent_cannot_silently_execute_the_scene_taught_program(policy_inputs):
    manager, policy, scene, suite, catalog = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene)
    assert subject.evaluationScene.id == suite.metadata["scene"]
    assert subject.evaluationScene.contentHash == suite.metadata["sceneContentHash"]
    assert subject.candidateIdentity.id == policy["id"]
    assert subject.binding["config"]["policy"] == policy["id"]
    assert subject.binding["interlocks"] == catalog.entities[subject.target.entityKey]["interlocks"]
    validate_base_catalog(catalog, subject)
    with pytest.raises(LibraryError) as error:
        validate_policy_catalog(catalog, subject)
    assert error.value.detail["code"] == "policy_binding_not_applied"
    assert catalog.entities[subject.target.entityKey]["program"] == "robot_arm.newton"
    assert verify_policy_subject(manager, subject.document(), suite, scene) == subject


def test_actual_replacement_keeps_every_non_target_binding_and_separate_scene_identity(policy_inputs):
    manager, policy, scene, suite, catalog = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene)
    original = deepcopy(catalog.entities)
    apply_policy_catalog(catalog, subject, scene)
    assert validate_policy_catalog(catalog, subject) == subject
    assert (
        scene.id == subject.evaluationScene.id and scene.content_hash == subject.evaluationScene.contentHash
    )
    for key, binding in original.items():
        if key != subject.target.entityKey:
            assert catalog.entities[key] == binding
    catalog.entities["/World/Lab/Centrifuge"]["config"]["visualRpmCap"] = 1.0
    with pytest.raises(LibraryError) as error:
        validate_policy_catalog(catalog, subject)
    assert error.value.detail["code"] == "policy_binding_not_applied"


def test_non_target_drift_rejects_before_any_override(policy_inputs):
    manager, policy, scene, suite, catalog = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene)
    catalog.entities["/World/Lab/Centrifuge"]["interlocks"]["closeLid"] = []
    with pytest.raises(LibraryError) as error:
        validate_base_catalog(catalog, subject)
    assert error.value.detail["code"] == "policy_base_binding_changed"


def test_checkpoint_intent_reuses_the_existing_exact_frozen_policy(policy_inputs):
    manager, policy, scene, suite, _ = policy_inputs
    subject = resolve_policy_subject(manager, "checkpoint/" + policy["checkpoint"], suite, scene)
    assert subject.candidateIdentity.id == policy["id"]
    assert len(manager.policies.list()) == 1
    assert verify_policy_subject(manager, subject, suite, scene) == subject


def test_foreign_scene_and_source_never_fall_back_to_a_registered_program(policy_inputs):
    manager, policy, scene, suite, _ = policy_inputs
    with pytest.raises(LibraryError):
        resolve_policy_subject(manager, policy["id"], suite, replace(scene, content_hash="a" * 64))
    suite.metadata["source"] = "synthetic"
    with pytest.raises(LibraryError):
        resolve_policy_subject(manager, policy["id"], suite, scene)


def test_tampered_policy_and_tampered_frozen_descriptor_are_rejected(policy_inputs):
    manager, policy, scene, suite, catalog = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene)
    altered = subject.document()
    altered["binding"]["config"]["policy"] = "policy/another@1"
    with pytest.raises(LibraryError):
        validate_base_catalog(catalog, PolicySubject.model_validate(altered))
    _, package = manager.policies.resolve(policy["id"])
    (package / policy["onnx"]).write_bytes(b"changed policy bytes")
    with pytest.raises(LibraryError) as error:
        verify_policy_subject(manager, subject, suite, scene)
    assert error.value.detail["code"] == "policy_unavailable"


def test_native_receipt_must_match_the_exact_loaded_model_and_keep_zero_counts_honest(policy_inputs):
    manager, policy, scene, suite, _ = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene)
    receipt = {
        "kind": "policy",
        "policy": policy["id"],
        "policyContentHash": policy["contentHash"],
        "onnxSha256": policy["onnxSha256"],
        "observationSha256": policy["observationSha256"],
        "providers": ["CPUExecutionProvider"],
        "runtimeVersion": "fixture-counter-contract",
        "actionStarts": 0,
        "inferenceCalls": 0,
        "validOutputs": 0,
        "observationDigest": "a" * 64,
    }
    assert validate_policy_execution(subject, receipt)["inferenceCalls"] == 0
    common = {
        "available": True,
        "runtimeVersion": "fixture-counter-contract",
        "availableProviders": ["CPUExecutionProvider"],
    }
    assert validate_policy_execution(subject, receipt, runtime=common)["inferenceCalls"] == 0
    with pytest.raises(LibraryError):
        validate_policy_execution(subject, receipt, runtime=common | {"runtimeVersion": "other"})
    for change in (
        {"onnxSha256": "b" * 64},
        {"providers": ["CUDAExecutionProvider"]},
        {"inferenceCalls": 1},
        {"validOutputs": 1},
        {"inferenceCalls": True},
    ):
        with pytest.raises(LibraryError) as error:
            validate_policy_execution(subject, receipt | change)
        assert error.value.detail["code"] == "policy_execution_unavailable"


def test_deployment_material_matches_immutable_policy_and_controller_without_new_qualification(policy_inputs):
    manager, policy, scene, suite, _ = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene)
    identity = subject.candidateIdentity.model_dump(mode="json")
    controller = {"program": "robot_arm.policy", "policy": policy["id"], "programArtifact": None}
    assert validate_policy_material(identity, subject.binding, manager.library_assets.roots,
                                    controller=controller)["contentHash"] == policy["contentHash"]
    with pytest.raises(LibraryError):
        validate_policy_material(identity | {"contentHash": "0" * 64}, subject.binding,
                                 manager.library_assets.roots, controller=controller)


async def test_session_creation_requires_the_exact_admitted_policy_descriptor(policy_inputs):
    manager, policy, scene, suite, _ = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene).document()
    manager.evaluations.store.records["eval-frozen"] = {"policySubject": subject}
    with pytest.raises(HTTPException) as error:
        await manager.create(
            None, "newton", SessionOptions(device="cpu"), scene_version=scene.id, evaluation_id="eval-frozen"
        )
    assert error.value.detail["code"] == "policy_binding_required"
    with pytest.raises(HTTPException) as error:
        await manager.create(
            None, "newton", SessionOptions(device="cpu"), scene_version=scene.id, policy_subject=subject
        )
    assert error.value.detail["code"] == "policy_binding_required"
    altered = deepcopy(subject)
    altered["binding"]["config"]["maxSeconds"] += 1
    with pytest.raises(HTTPException) as error:
        await manager.create(
            None,
            "newton",
            SessionOptions(device="cpu"),
            scene_version=scene.id,
            evaluation_id="eval-frozen",
            policy_subject=altered,
        )
    assert error.value.detail["code"] == "policy_subject_changed"
    assert manager.sessions == {}


async def test_source_without_policy_override_support_is_closed_before_start(policy_inputs):
    manager, policy, scene, suite, _ = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene).document()

    class UnsupportedSource:
        started = closed = False

        def configure_scene(self, _):
            pass

        async def start(self, *_):
            self.started = True
            raise AssertionError("A policy intent must not start the taught fallback")

        async def close(self):
            self.closed = True

    source = UnsupportedSource()
    session = Session(
        scene.name,
        "newton",
        SessionOptions(device="cpu"),
        manager.settings,
        lambda *_: source,
        manager.recordings,
        manager.approvals,
        manager.plans,
        scene_version=scene,
        evaluation_id="eval-policy",
        policy_subject=subject,
    )
    with pytest.raises(ValueError, match="cannot apply"):
        await session.start()
    assert source.closed and not source.started
    assert session.source is None and session.recording is None


@pytest.mark.parametrize(
    "channel,unit", [("/World/Lab/RobotA/missing-joint", "rad"), ("/World/Lab/RobotA/fr3/fr3_joint1", "m")]
)
def test_model_channels_must_match_actual_bound_robot_roles_before_start(
    policy_inputs, policy_client, tmp_path, channel, unit
):
    manager, _, scene, suite, _ = policy_inputs
    spec = observation_spec()
    spec["action"]["jointKeys"] = [channel]
    spec["action"]["units"] = [unit]
    job = uploaded_job(policy_client, tmp_path / "invalid-channel", spec=spec)
    created = policy_client.post(
        "/api/v1/policies",
        json={
            "checkpoint": job["jobId"] + "/checkpoints/model_0.pt",
        },
    )
    assert created.status_code == 201, created.text
    with pytest.raises(LibraryError) as error:
        resolve_policy_subject(manager, created.json()["id"], suite, scene)
    assert error.value.detail["code"] == "policy_action_binding_mismatch"
    assert manager.sessions == {}
