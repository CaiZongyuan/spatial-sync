"""Policy identity and actual Catalog binding integrity, with no engine startup."""

from copy import deepcopy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import LibraryError
from packages.library.previews import PreviewRepository
from packages.policies.subjects import (
    PolicySubject,
    resolve_policy_subject,
    validate_base_catalog,
    validate_policy_catalog,
    verify_policy_subject,
)
from packages.programs.robot_arm.policy import PolicyProgram
from packages.protocol.schema import SceneManifest
from packages.sim_runtime.info_catalog import load_info_catalog
from tests.fixtures.policy_evaluation import native_suite
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import TOKEN


@pytest.fixture
def policy_inputs(tmp_path):
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        job = uploaded_job(client, tmp_path)
        created = client.post(
            "/api/v1/policies",
            json={
                "checkpoint": job["jobId"] + "/checkpoints/model_0.pt",
            },
        )
        assert created.status_code == 201, created.text
        manager, policy = app.state.manager, created.json()
        scene = manager.library.resolve(policy["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene)
        preview = PreviewRepository(manager.settings.scene_previews_root)
        manifest = SceneManifest.model_validate_json(preview.read(scene, "newton", "manifest.json"))
        catalog = load_info_catalog(manifest, scene.name, scene_package=scene)
        yield manager, policy, scene, suite, catalog


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
    # Producer fixture. The real Runner hook will perform this replacement only after base validation.
    catalog.entities[subject.target.entityKey] = deepcopy(subject.binding)
    catalog._configs[subject.target.entityKey] = PolicyProgram.Config.model_validate(
        subject.binding["config"]
    )
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
