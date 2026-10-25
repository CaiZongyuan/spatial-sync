"""Exact script material and code approval, separate from fixed simulation binding facts."""

from typing import Literal

from pydantic import Field

from packages.library.common import LibraryError
from packages.programs.authorization import ProgramApproval, require_program_approval
from packages.programs.library import ProgramLibrary
from packages.programs.metadata import Hash, ProgramIntent, Schema, Text
from packages.programs.robot_arm.script import ScriptProgram
from packages.programs.subjects import (
    BindingTarget,
    EvaluationScene,
    apply_binding,
    fingerprint,
    fixed_bindings,
    validate_applied_catalog,
    validate_base_catalog,
)

MATERIAL = ("contentHash", "codeHash", "configHash", "archiveSha256")
CALLBACKS = {"init", "accept", "start", "tick", "cancel"}


class ProgramCandidate(ProgramIntent):
    kind: Literal["program"] = "program"
    codeHash: Hash
    configHash: Hash
    archiveSha256: Hash
    planId: Text
    planRevision: int = Field(ge=1)
    approvalId: Text
    task: Text
    taskContentHash: Hash


class Target(BindingTarget):
    interface: Literal["robot-arm/v1"]


class ScriptSubject(Schema):
    schema_version: Literal["lw.script-subject/1"] = Field(default="lw.script-subject/1", alias="schema")
    requestedSubject: Text
    candidateIdentity: ProgramCandidate
    codeApproval: ProgramApproval
    evaluationScene: EvaluationScene
    target: Target
    baseBindings: dict[str, Hash]
    binding: dict
    bindingHash: Hash

    def document(self):
        return self.model_dump(mode="json")


class ProgramExecution(Schema):
    kind: Literal["program"]
    program: Text
    programContentHash: Hash
    codeHash: Hash
    configHash: Hash
    archiveSha256: Hash
    actionStarts: int = Field(ge=0)
    callbacks: dict[str, int]
    callbackErrors: int = Field(ge=0)


def invalid(message, code="program_subject_mismatch"):
    return LibraryError(code, message, 409)


def linked_subject(subject):
    subject = ScriptSubject.model_validate(subject) if isinstance(subject, dict) else subject
    candidate, proof = subject.candidateIdentity, subject.codeApproval
    if (
        candidate.id != proof.program.id
        or candidate.contentHash != proof.program.contentHash
        or any(
            getattr(candidate, name) != getattr(proof, name)
            for name in ("planId", "planRevision", "approvalId", "task", "taskContentHash")
        )
        or subject.requestedSubject != candidate.id
        or subject.binding.get("program") != ScriptProgram.id
        or subject.binding.get("key") != subject.target.entityKey
        or subject.binding.get("type") != subject.target.interface
        or subject.binding.get("config", {}).get("programArtifact") != proof.program.model_dump(mode="json")
        or fingerprint(subject.binding) != subject.bindingHash
    ):
        raise invalid("Frozen script replacement, material and code approval differ")
    return subject


def resolve_program_subject(manager, requested, suite, scene, *, plan_id, plan_revision=None):
    from packages.sim_runtime.info_catalog import REGISTRY

    proof, record = require_program_approval(
        manager, requested, suite, scene, plan_id=plan_id, plan_revision=plan_revision
    )
    bindings = fixed_bindings(scene, "newton", REGISTRY)
    interface, action = suite.task["implements"].rsplit(".", 1)
    base = bindings.get(suite.task["robot"])
    if base is None or base["type"] != ScriptProgram.type or interface != ScriptProgram.type:
        raise invalid("Script target is absent or has a different interface")
    config = {
        name: value for name, value in base["config"].items() if name in ScriptProgram.Config.model_fields
    }
    config.update(
        programArtifact=proof.program.model_dump(mode="json"), parameters=record["metadata"]["config"]
    )
    config = ScriptProgram.Config.model_validate(config).model_dump(mode="json")
    binding = base | {"program": ScriptProgram.id, "config": config}
    return linked_subject(
        ScriptSubject(
            requestedSubject=requested,
            candidateIdentity=ProgramCandidate(
                id=record["id"],
                **{name: record[name] for name in MATERIAL},
                **{
                    name: getattr(proof, name)
                    for name in ("planId", "planRevision", "approvalId", "task", "taskContentHash")
                },
            ),
            codeApproval=proof,
            evaluationScene=EvaluationScene(id=scene.id, contentHash=scene.content_hash, source="newton"),
            target=Target(entityKey=suite.task["robot"], interface=interface, action=action),
            baseBindings={key: fingerprint(value) for key, value in bindings.items()},
            binding=binding,
            bindingHash=fingerprint(binding),
        )
    )


def verify_program_subject(manager, subject, suite, scene):
    subject = linked_subject(subject)
    actual = resolve_program_subject(
        manager,
        subject.candidateIdentity.id,
        suite,
        scene,
        plan_id=subject.codeApproval.planId,
        plan_revision=subject.codeApproval.planRevision,
    )
    if actual != subject:
        raise invalid("Frozen script subject changed after admission", "program_subject_changed")
    return subject


def check_program_material(subject, library):
    """Runner uses this before importing any Candidate module."""
    subject = linked_subject(subject)
    record = library.show(subject.candidateIdentity.id)
    if (
        any(record[name] != getattr(subject.candidateIdentity, name) for name in MATERIAL)
        or record["metadata"]["implements"] != subject.target.interface + "." + subject.target.action
        or fingerprint(record["metadata"]["config"])
        != fingerprint(subject.binding["config"].get("parameters"))
    ):
        raise invalid("Actual script material or reviewed configuration differs", "program_subject_changed")
    return record


def apply_script_catalog(catalog, subject, scene):
    subject = linked_subject(subject)
    try:
        validate_base_catalog(catalog, subject)
        return apply_binding(catalog, subject, scene, {ScriptProgram.id: ScriptProgram})
    except LibraryError as error:
        raise invalid(
            "Actual script base Scene or replacement differs", "program_binding_not_applied"
        ) from error


def validate_script_catalog(catalog, subject):
    subject = linked_subject(subject)
    try:
        validate_applied_catalog(catalog, subject)
    except LibraryError as error:
        raise invalid(
            "Actual script replacement is missing or differs", "program_binding_not_applied"
        ) from error
    return subject


def validate_program_execution(subject, evidence, *, runtime=None):
    subject = linked_subject(subject)
    try:
        actual = ProgramExecution.model_validate(evidence)
        candidate = subject.candidateIdentity
        if (
            actual.program != candidate.id
            or actual.programContentHash != candidate.contentHash
            or any(
                getattr(actual, name) != getattr(candidate, name)
                for name in ("codeHash", "configHash", "archiveSha256")
            )
            or set(actual.callbacks) != CALLBACKS
            or any(type(count) is not int or count < 0 for count in actual.callbacks.values())
            or actual.callbacks["init"] != 1
            or actual.callbacks["start"] != actual.actionStarts
            or actual.callbackErrors > sum(actual.callbacks.values())
            or actual.callbacks["tick"] > 0
            and actual.actionStarts == 0
        ):
            raise ValueError("Actual script identity or callback counts differ")
        return actual.model_dump(mode="json")
    except (ValueError, TypeError) as error:
        raise invalid(
            "Native script execution evidence is unavailable or differs", "program_execution_unavailable"
        ) from error


def validate_program_material(candidate, binding, roots, *, controller=None):
    identity = ProgramCandidate.model_validate(candidate)
    record = ProgramLibrary(roots).show(identity.id)
    artifact = {"id": identity.id, "contentHash": identity.contentHash}
    if (
        binding.get("program") != ScriptProgram.id
        or binding.get("type") != ScriptProgram.type
        or binding.get("config", {}).get("programArtifact") != artifact
        or any(record[name] != getattr(identity, name) for name in MATERIAL)
        or fingerprint(binding["config"].get("parameters")) != fingerprint(record["metadata"]["config"])
    ):
        raise invalid("Deployed script material or reviewed configuration differs", "program_subject_changed")
    if controller is not None and (
        controller.get("program") != ScriptProgram.id or controller.get("programArtifact") != artifact
    ):
        raise invalid("Deployed Controller/script material differs")
    return record
