"""Gateway and Runner derive authority from committed files plus the exact human audit."""

from contextlib import nullcontext
from dataclasses import replace

import pytest

from packages.info.plans import PlanError
from packages.library.transactions import durable_json
from packages.programs.subjects import fingerprint
from packages.protocol.schema import Actor
from packages.training.deployment_publication import DeploymentPublication, read_committed_permit
from tests.unit.test_controller_publication import publication_inputs  # noqa: F401
from tests.unit.test_policy_subjects import policy_client, policy_inputs  # noqa: F401


def committed(inputs):
    manager, resources, importer, plan, _, _ = inputs
    context = {
        "baseScene": {"id": plan.scene.id, "contentHash": plan.scene.content_hash},
        "subject": plan.subject.document(),
        "controller": plan.controller,
        "versionFields": plan.version_fields,
        "libraryRoot": str(manager.library_assets.roots["user"]),
        "materialIdentities": {plan.subject.target.entityKey: plan.subject.candidateIdentity.model_dump()},
        "evidenceMode": "controlled-transaction-fixture",
    }
    plan = replace(plan, context_hash=fingerprint(context))
    approval = manager.approvals.submit(
        subject="policy",
        subject_id=plan.promotion_id,
        requester=Actor(kind="agent", name="fixture"),
        title="Controlled deployment transaction",
        details={"controllerPromotionId": plan.promotion_id, "contextHash": plan.context_hash},
    )
    manager.approvals.decide(approval.approvalId, "approve", Actor(kind="user", name="fixture-human"))
    root = manager.settings.state_root / "controller-promotions"
    durable_json(
        root / f"{plan.promotion_id}.json",
        {
            "schema": "lw.controller-promotion/1",
            "id": plan.promotion_id,
            "status": "approved",
            "context": context,
            "contextHash": plan.context_hash,
            "approvalId": approval.approvalId,
        },
    )
    publisher = DeploymentPublication(
        manager.library_assets,
        manager.library,
        resources,
        importer,
        root,
        validate_context=lambda _: None,
        commit_context=lambda _: nullcontext(),
    )
    publisher.prepare(plan)
    assert publisher.publish(plan.promotion_id)["phase"] == "committed"
    return manager, plan, approval


def test_exact_committed_human_proof_is_readable_without_mutation_and_survives_training_revoke(
    publication_inputs,  # noqa: F811
):
    manager, plan, approval = committed(publication_inputs)
    permit = read_committed_permit(manager.settings.state_root, plan.promotion_id)
    assert (
        permit["materialIdentities"][plan.subject.target.entityKey]
        == plan.subject.candidateIdentity.model_dump()
    )
    assert (
        read_committed_permit(manager.settings.state_root, plan.promotion_id, permit["publicationHash"])
        == permit
    )
    job = manager.jobs.get(plan.subject.candidateIdentity.jobId)
    training = next(
        value
        for value in manager.approvals.records.values()
        if value.subject == "plan" and value.subjectId == job["planId"]
    )
    manager.approvals.decide(training.approvalId, "revoke", Actor(kind="user", name="fixture-human"))
    assert read_committed_permit(manager.settings.state_root, plan.promotion_id) == permit
    with pytest.raises(PlanError):
        read_committed_permit(manager.settings.state_root, plan.promotion_id, "f" * 64)
    manager.approvals.decide(approval.approvalId, "revoke", Actor(kind="user", name="fixture-human"))
    with pytest.raises(PlanError):
        read_committed_permit(manager.settings.state_root, plan.promotion_id)


def test_actual_controller_material_and_committed_state_are_required(publication_inputs):  # noqa: F811
    manager, plan, _ = committed(publication_inputs)
    root = manager.settings.state_root / "controller-promotions"
    path = root / "controller-publications" / plan.promotion_id / "record.json"
    import json

    record = json.loads(path.read_bytes())
    record["phase"] = "controller_version_written"
    durable_json(path, record)
    with pytest.raises(PlanError):
        read_committed_permit(manager.settings.state_root, plan.promotion_id)
