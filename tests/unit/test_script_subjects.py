"""Exact script Candidate and physical binding facts remain inert in Gateway metadata paths."""

import json
import math
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.info.plans import PlanError
from packages.library.common import REPO_ROOT, LibraryError
from packages.programs.script_subjects import (
    apply_script_catalog,
    check_program_material,
    resolve_program_subject,
    validate_script_catalog,
    verify_program_subject,
)
from packages.programs.subjects import fingerprint
from packages.protocol.schema import SceneManifest
from packages.sim_runtime.info_catalog import load_info_catalog
from tests.fixtures.embodied_evaluation import native_suite
from tests.gateway.test_program_plans import prepare


@pytest.fixture
def script_inputs(tmp_path):
    def forbidden(*_):
        raise AssertionError("Script metadata paths cannot start a Source")

    with TestClient(create_app(source_factory=forbidden)) as client:
        body, snapshot = prepare(client, tmp_path)
        plan = client.post("/api/v1/plans", json=body).json()
        assert (
            client.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        manager = client.app.state.manager
        scene = manager.library.resolve(body["evaluation"]["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene)
        path = (
            REPO_ROOT / "assets/scene-previews" / scene.name / str(scene.rev) / scene.content_hash / "newton"
        )
        capture = json.loads((path / "capture.json").read_bytes())
        assert capture["sceneContentHash"] == scene.content_hash
        manifest = SceneManifest.model_validate_json((path / "manifest.json").read_bytes())
        catalog = load_info_catalog(manifest, scene.name, scene_package=scene)
        yield client, manager, scene, suite, snapshot, plan, catalog
        assert not hasattr(math, "_lw_uploaded_program_marker")


def subject(inputs):
    _, manager, scene, suite, snapshot, plan, _ = inputs
    return resolve_program_subject(manager, snapshot["id"], suite, scene, plan_id=plan["id"], plan_revision=1)


def test_script_subject_retains_known_code_and_exact_review_proof_before_source(script_inputs):
    client, manager, scene, suite, snapshot, plan, catalog = script_inputs
    value = subject(script_inputs)
    assert value.candidateIdentity.id == snapshot["id"]
    for field in ("contentHash", "codeHash", "configHash", "archiveSha256"):
        assert getattr(value.candidateIdentity, field) == snapshot[field]
    assert value.codeApproval.approvalId == plan["approvalId"]
    assert value.candidateIdentity.planId == plan["id"]
    assert value.binding["config"]["programArtifact"] == plan["program"]
    assert value.binding["config"]["parameters"] == snapshot["metadata"]["config"]
    assert value.binding["interlocks"] == catalog.entities[value.target.entityKey]["interlocks"]
    assert verify_program_subject(manager, value.document(), suite, scene) == value
    assert check_program_material(value, manager.programs)["contentHash"] == snapshot["contentHash"]
    assert client.get("/api/v1/health").json()["sessionCount"] == 0


def test_script_replacement_preserves_complete_non_target_catalog_and_rejects_drift(script_inputs):
    _, _, scene, _, _, _, catalog = script_inputs
    value = subject(script_inputs)
    original = deepcopy(catalog.entities)
    with pytest.raises(LibraryError, match="replacement"):
        validate_script_catalog(catalog, value)
    apply_script_catalog(catalog, value, scene)
    assert validate_script_catalog(catalog, value) == value
    assert catalog.entities[value.target.entityKey] == value.binding
    assert {key: binding for key, binding in original.items() if key != value.target.entityKey} == {
        key: binding for key, binding in catalog.entities.items() if key != value.target.entityKey
    }
    catalog.entities["/World/Lab/Centrifuge"]["config"]["visualRpmCap"] = 1.0
    with pytest.raises(LibraryError):
        validate_script_catalog(catalog, value)


def test_revised_or_revoked_code_plan_cannot_reuse_a_frozen_admitted_subject(script_inputs):
    client, manager, scene, suite, _, plan, _ = script_inputs
    value = subject(script_inputs)
    assert (
        client.post(
            "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "revoke"}
        ).status_code
        == 200
    )
    with pytest.raises(PlanError):
        verify_program_subject(manager, value, suite, scene)


def test_material_check_rejects_changed_parameters_before_uploaded_import(script_inputs):
    _, manager, _, _, _, _, _ = script_inputs
    value = subject(script_inputs).document()
    value["binding"]["config"]["parameters"] = {"unreviewed": True}
    value["bindingHash"] = fingerprint(value["binding"])
    with pytest.raises(LibraryError):
        check_program_material(value, manager.programs)
    assert not hasattr(math, "_lw_uploaded_program_marker")
