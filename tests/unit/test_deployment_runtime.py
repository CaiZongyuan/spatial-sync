"""Controlled authority result isolates Runtime Catalog/material checks; no Source or model executes."""

from copy import deepcopy

import pytest

from packages.info.plans import PlanError
from packages.library.common import LibraryError
from packages.programs import deployment
from packages.programs.script_subjects import apply_script_catalog
from packages.programs.subjects import fingerprint
from tests.unit.test_script_subjects import script_inputs as script_inputs
from tests.unit.test_script_subjects import subject


def permit_inputs(inputs):
    _, manager, scene, _, _, _, catalog = inputs
    value = subject(inputs)
    apply_script_catalog(catalog, value, scene)
    identity = value.candidateIdentity.model_dump(mode="json")
    permit = {
        "schema": "lw.deployment-permit/1",
        "promotionId": "promotion-runtime-fixture",
        "approvalId": "approval-controlled",
        "contextHash": "a" * 64,
        "scene": {"id": scene.id, "contentHash": scene.content_hash},
        "controller": {
            "id": "fixture",
            "version": 1,
            "contentHash": "b" * 64,
            "document": {
                "controller": "fixture",
                "version": 1,
                "scene": scene.id,
                "program": "robot_arm.script",
                "programArtifact": value.codeApproval.program.model_dump(),
            },
        },
        "candidateIdentity": identity,
        "materialIdentities": {value.target.entityKey: identity},
        "target": value.target.model_dump(),
        "binding": value.binding,
        "bindingHash": value.bindingHash,
        "appliedBindings": {key: fingerprint(binding) for key, binding in catalog.entities.items()},
    }
    permit["publicationHash"] = fingerprint(permit)
    return manager, scene, catalog, permit


def test_runtime_permit_rebuilds_actual_authority_and_verifies_complete_material_binding_map(
    script_inputs, monkeypatch
):
    manager, scene, catalog, permit = permit_inputs(script_inputs)
    calls = []

    def actual(state, promotion, publication):
        calls.append((state, promotion, publication))
        return deepcopy(permit)

    monkeypatch.setattr(deployment, "read_committed_permit", actual)
    value = deployment.verify_runtime_deployment(
        scene, catalog, permit, state_root=manager.settings.state_root
    )
    assert value.permit == permit and value.bindings == catalog.entities
    assert calls == [(manager.settings.state_root, permit["promotionId"], permit["publicationHash"])]
    catalog.entities["/World/Lab/Centrifuge"]["config"]["visualRpmCap"] = 1.0
    with pytest.raises(LibraryError):
        deployment.verify_runtime_deployment(scene, catalog, permit, state_root=manager.settings.state_root)


def test_a_package_plan_approval_does_not_replace_actual_committed_publication(script_inputs):
    manager, scene, catalog, permit = permit_inputs(script_inputs)
    with pytest.raises(PlanError) as error:
        deployment.verify_runtime_deployment(scene, catalog, permit, state_root=manager.settings.state_root)
    assert error.value.detail["code"] == "deployment_authorization_required"


@pytest.mark.parametrize("case", ["flag", "wire", "parameters", "material", "coverage", "controller"])
def test_runtime_checks_do_not_accept_caller_flags_or_self_consistent_unreviewed_material(
    script_inputs, monkeypatch, case
):
    manager, scene, catalog, permit = permit_inputs(script_inputs)
    frozen = deepcopy(permit)
    key = permit["target"]["entityKey"]
    if case == "flag":
        permit = {"approved": True}
    elif case == "wire":
        permit["contextHash"] = "c" * 64
    elif case == "parameters":
        catalog.entities[key]["config"]["parameters"] = {"unreviewed": 1}
        permit["binding"] = deepcopy(catalog.entities[key])
        permit["bindingHash"] = fingerprint(permit["binding"])
        permit["appliedBindings"][key] = permit["bindingHash"]
    elif case == "material":
        permit["materialIdentities"][key]["contentHash"] = "c" * 64
        permit["candidateIdentity"]["contentHash"] = "c" * 64
    elif case == "coverage":
        permit["materialIdentities"] = {}
    else:
        permit["controller"]["document"]["programArtifact"]["contentHash"] = "c" * 64
    if case != "flag":
        permit["publicationHash"] = fingerprint(
            {name: item for name, item in permit.items() if name != "publicationHash"}
        )
    # Wire changes must still match real authority; other cases deliberately exercise the
    # independent Runtime material/config checks after an authority-provider fixture.
    actual = frozen if case == "wire" else permit
    monkeypatch.setattr(deployment, "read_committed_permit", lambda *_: deepcopy(actual))
    with pytest.raises(LibraryError):
        deployment.verify_runtime_deployment(scene, catalog, permit, state_root=manager.settings.state_root)
