"""Generic private descriptors retain legacy policy compatibility and exact admission ownership."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.api.app.session_manager import Session
from apps.api.app.sources.newton_source import NewtonSource
from packages.info.plans import PlanError
from packages.library.common import LibraryError
from packages.programs.simulation import (
    admitted_subject,
    apply_simulation_catalog,
    parse_subject,
    select_subject,
    validate_simulation_catalog,
)
from packages.programs.subjects import fingerprint
from packages.protocol.schema import SessionOptions
from tests.unit.test_script_subjects import script_inputs as script_inputs
from tests.unit.test_script_subjects import subject


def legacy_policy():
    binding = {"key": "/robot", "type": "robot-arm/v1", "program": "robot_arm.policy", "config": {}}
    return {
        "schema": "lw.policy-subject/1",
        "requestedSubject": "policy/fixture@1",
        "candidateIdentity": {
            "kind": "policy",
            "id": "policy/fixture@1",
            "contentHash": "a" * 64,
            "jobId": "job-fixture",
            "checkpoint": "job-fixture/model.pt",
            "checkpointSha256": "b" * 64,
            "onnxSha256": "c" * 64,
            "observationSha256": "d" * 64,
            "task": "task/fixture@1",
            "taskContentHash": "e" * 64,
        },
        "evaluationScene": {"id": "scene@1", "contentHash": "f" * 64, "source": "newton"},
        "target": {"entityKey": "/robot", "interface": "robot-arm/v1", "action": "pickPlace"},
        "baseBindings": {"/robot": "a" * 64},
        "binding": binding,
        "bindingHash": fingerprint(binding),
    }


def test_legacy_policy_document_and_alias_remain_readable_without_class_on_wire():
    document = legacy_policy()
    assert parse_subject(document).document() == document
    assert select_subject(policy_subject=document).document() == document
    assert select_subject(simulation_subject=document, policy_subject=document).document() == document
    assert admitted_subject({"policySubject": document}).document() == document
    changed = deepcopy(document)
    changed["binding"]["config"]["different"] = True
    with pytest.raises(LibraryError):
        select_subject(simulation_subject=changed, policy_subject=document)
    with pytest.raises(LibraryError):
        parse_subject({"schema": "unrecognized"})


def test_generic_script_catalog_path_is_inert_and_legacy_policy_alias_cannot_carry_script(script_inputs):
    _, _, scene, _, _, _, catalog = script_inputs
    value = subject(script_inputs)
    assert admitted_subject({"simulationSubject": value.document()}) == value
    with pytest.raises(LibraryError):
        select_subject(policy_subject=value.document())
    apply_simulation_catalog(catalog, value.document(), scene)
    assert validate_simulation_catalog(catalog, value.document()) == value


async def test_script_session_requires_the_exact_admitted_private_descriptor(script_inputs):
    _, manager, scene, _, _, _, _ = script_inputs
    value = subject(script_inputs).document()
    manager.evaluations.store.records["eval-script-frozen"] = {"simulationSubject": value}
    with pytest.raises(HTTPException) as error:
        await manager.create(
            None,
            "newton",
            SessionOptions(device="cpu"),
            scene_version=scene.id,
            evaluation_id="eval-script-frozen",
        )
    assert error.value.detail["code"] == "program_binding_required"
    with pytest.raises(HTTPException) as error:
        await manager.create(
            None, "newton", SessionOptions(device="cpu"), scene_version=scene.id, simulation_subject=value
        )
    assert error.value.detail["code"] == "program_binding_required"
    changed = deepcopy(value)
    changed["binding"]["config"]["maxSeconds"] += 1
    with pytest.raises(HTTPException) as error:
        await manager.create(
            None,
            "newton",
            SessionOptions(device="cpu"),
            scene_version=scene.id,
            evaluation_id="eval-script-frozen",
            simulation_subject=changed,
        )
    assert error.value.detail["code"] == "program_subject_changed"
    assert manager.sessions == {}


async def test_package_approval_and_an_ordinary_scene_import_do_not_authorize_loading(
    script_inputs, monkeypatch
):
    _, manager, _, _, _, _, _ = script_inputs
    scene = SimpleNamespace(
        id="unpublished@1",
        name="unpublished",
        metadata={"sources": ["newton"], "stage": "python:owned.fixture"},
        report=lambda: {"effectiveBindings": {"newton": [{"program": "robot_arm.script"}]}},
    )
    monkeypatch.setattr(manager.library, "refresh", lambda: None)
    monkeypatch.setattr(manager.library, "resolve", lambda *_args, **_kwargs: scene)
    with pytest.raises(PlanError) as error:
        await manager.create(None, "newton", SessionOptions(device="cpu"), scene_version=scene.id)
    assert error.value.detail["code"] == "deployment_authorization_required"
    assert error.value.status == 409
    assert manager.sessions == {} and manager._starting_session is None


async def test_script_source_without_execution_context_support_rejects_before_start(script_inputs):
    _, manager, scene, _, _, _, _ = script_inputs

    class Unsupported:
        started = closed = False

        def configure_scene(self, _):
            pass

        def configure_evaluation(self, _):
            pass

        async def start(self, *_):
            self.started = True
            raise AssertionError("Unsupported source cannot execute script code")

        async def close(self):
            self.closed = True

    source = Unsupported()
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
        evaluation_id="eval-script",
        simulation_subject=subject(script_inputs).document(),
    )
    with pytest.raises(ValueError, match="execution context"):
        await session.start()
    assert source.closed and not source.started and session.source is None


async def test_newton_private_configuration_retains_script_json_and_explicit_state_root(
    script_inputs, tmp_path
):
    value = subject(script_inputs).document()
    source = NewtonSource("owned-script-config")
    source.configure_evaluation(value)
    source.configure_execution(state_root=tmp_path)
    assert source.simulation_subject == value and source.policy_subject is None
    assert source.execution_state_root == tmp_path.resolve()
    await source.close()
