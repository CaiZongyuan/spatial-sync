"""Freeze policy identity separately from the fixed evaluation world and its bindings."""

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.info.declarations import EntityBindingDeclaration
from packages.library.common import LibraryError, document
from packages.programs.robot_arm.policy import PolicyProgram
from packages.programs.subjects import (
    apply_binding,
    fingerprint,
    validate_applied_catalog,
)
from packages.programs.subjects import (
    validate_base_catalog as validate_simulation_base,
)

from .library import PolicyLibrary, read_json
from .observations import ObservationSpec

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1)]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, serialize_by_alias=True)


class CandidateIdentity(Schema):
    kind: Literal["policy"] = "policy"
    id: str = Field(pattern=r"^policy/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")
    contentHash: Hash
    jobId: Text
    checkpoint: Text
    checkpointSha256: Hash
    onnxSha256: Hash
    observationSha256: Hash
    task: Text
    taskContentHash: Hash


class EvaluationScene(Schema):
    id: Text
    contentHash: Hash
    source: Literal["newton"]


class Target(Schema):
    entityKey: Text
    interface: Literal["robot-arm/v1"]
    action: Text


class PolicySubject(Schema):
    schema_version: Literal["lw.policy-subject/1"] = Field(default="lw.policy-subject/1", alias="schema")
    requestedSubject: Text
    candidateIdentity: CandidateIdentity
    evaluationScene: EvaluationScene
    target: Target
    baseBindings: dict[Text, Hash]
    binding: dict
    bindingHash: Hash

    def document(self):
        return self.model_dump(mode="json")


class PolicyExecution(Schema):
    kind: Literal["policy"]
    policy: Text
    policyContentHash: Hash
    onnxSha256: Hash
    observationSha256: Hash
    providers: list[Literal["CPUExecutionProvider"]] = Field(min_length=1, max_length=1)
    runtimeVersion: Text
    actionStarts: int = Field(ge=0)
    inferenceCalls: int = Field(ge=0)
    validOutputs: int = Field(ge=0)
    observationDigest: Hash


def _invalid(message, code="policy_subject_mismatch"):
    return LibraryError(code, message, 409)


def canonical_binding(binding, registry):
    """Match the public Catalog's normalization, including current Program defaults."""
    value = deepcopy(binding)
    program = registry[value["program"]]
    if value["type"] != program.type:
        raise ValueError("Declared Program/interface differ")
    value["config"] = program.Config.model_validate(value["config"]).model_dump(mode="json")
    return EntityBindingDeclaration.model_validate(value).model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
        exclude={"alerts"},
    )


def fixed_bindings(scene, source):
    """Use the validated instantiated declarations; never guess a missing robot or document."""
    from packages.sim_runtime.info_catalog import REGISTRY

    scene.require_launchable()
    try:
        bindings = scene.report()["effectiveBindings"][source]
        if not bindings:
            raise ValueError("No verified effective bindings")
        result = {value["key"]: canonical_binding(value, REGISTRY) for value in bindings}
        if len(result) != len(bindings):
            raise ValueError("Duplicate effective binding")
        return result
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid("Verified evaluation bindings are unavailable", "policy_binding_unavailable") from exc


def _candidate(manager, requested):
    if requested.startswith("policy/"):
        return manager.policies.resolve(requested)
    checkpoint = requested.removeprefix("checkpoint/")
    if not checkpoint.startswith("job-") or "/" not in checkpoint:
        raise LibraryError("unsupported_subject", "Select an exact policy or Job checkpoint", 422)
    policy = manager.policies.create(manager, checkpoint)
    return manager.policies.resolve(policy["id"])


def resolve_policy_subject(manager, requested, suite, scene):
    """No Source starts here. The caller persists this descriptor before reserving one."""
    suite.verify()
    if (
        suite.metadata["layer"] != "embodied"
        or suite.metadata["source"] != "newton"
        or "newton" not in scene.metadata["sources"]
        or scene.id != suite.metadata["scene"]
        or scene.content_hash != suite.metadata["sceneContentHash"]
    ):
        raise _invalid("Policy evaluation requires the fixed Newton Suite Scene")
    policy, package = _candidate(manager, requested)
    task = read_json(package / "task.json")
    interface, action = suite.task["implements"].rsplit(".", 1)
    if (
        policy["scene"] != scene.id
        or policy["sceneContentHash"] != scene.content_hash
        or task["scene"] != scene.id
        or task["robot"] != suite.task["robot"]
        or task["implements"] != suite.task["implements"]
        or interface != PolicyProgram.type
    ):
        raise _invalid("Policy Task and fixed evaluation target differ")
    bindings = fixed_bindings(scene, "newton")
    base = bindings.get(task["robot"])
    if base is None or base["type"] != interface:
        raise _invalid("Policy target is absent or has a different interface")
    config = {
        name: value
        for name, value in base["config"].items()
        if name in PolicyProgram.Config.model_fields and name != "policy"
    }
    config["policy"] = policy["id"]
    config = PolicyProgram.Config.model_validate(config).model_dump(mode="json")
    spec = ObservationSpec.model_validate(document(package / "obs_spec.yaml"))
    allowed = set(config["jointKeys"] + config["fingerKeys"])
    units = ["m" if key in config["fingerKeys"] else "rad" for key in spec.action.jointKeys]
    if not set(spec.action.jointKeys) <= allowed or spec.action.units != units:
        raise _invalid(
            "Policy action channels do not match the fixed robot binding", "policy_action_binding_mismatch"
        )
    replacement = base | {"program": PolicyProgram.id, "config": config}
    candidate = {name: policy[name] for name in CandidateIdentity.model_fields if name in policy}
    return PolicySubject(
        requestedSubject=requested,
        candidateIdentity=CandidateIdentity(**candidate),
        evaluationScene=EvaluationScene(id=scene.id, contentHash=scene.content_hash, source="newton"),
        target=Target(entityKey=task["robot"], interface=interface, action=action),
        baseBindings={key: fingerprint(value) for key, value in bindings.items()},
        binding=replacement,
        bindingHash=fingerprint(replacement),
    )


def verify_policy_subject(manager, subject, suite, scene):
    """Re-resolve immutable bytes and declarations; no cached descriptor authorizes drift."""
    subject = PolicySubject.model_validate(subject) if isinstance(subject, dict) else subject
    current = resolve_policy_subject(manager, subject.candidateIdentity.id, suite, scene)
    expected = subject.document() | {"requestedSubject": current.requestedSubject}
    if current.document() != expected:
        raise _invalid("Frozen policy subject changed after admission", "policy_subject_changed")
    return subject


def validate_base_catalog(catalog, subject):
    """Every target and non-target definition must match before applying an override."""
    subject = PolicySubject.model_validate(subject) if isinstance(subject, dict) else subject
    try:
        validate_simulation_base(catalog, subject)
    except LibraryError as exc:
        if exc.detail["code"] == "subject_base_binding_changed":
            raise _invalid("Actual evaluation base bindings differ", "policy_base_binding_changed") from exc
        if fingerprint(subject.binding) != subject.bindingHash:
            raise _invalid("Frozen replacement binding changed", "policy_subject_changed") from exc
        raise _invalid("Replacement is not the frozen policy target") from exc
    if (
        subject.binding.get("program") != PolicyProgram.id
        or subject.binding.get("key") != subject.target.entityKey
        or subject.binding.get("type") != subject.target.interface
        or subject.binding.get("config", {}).get("policy") != subject.candidateIdentity.id
    ):
        raise _invalid("Replacement is not the frozen policy target")


def validate_policy_catalog(catalog, subject):
    """A declared policy intent must never silently execute the scene's taught Program."""
    subject = PolicySubject.model_validate(subject) if isinstance(subject, dict) else subject
    try:
        validate_applied_catalog(catalog, subject)
    except LibraryError as exc:
        raise _invalid("Actual policy override is missing or differs", "policy_binding_not_applied") from exc
    return subject


def apply_policy_catalog(catalog, subject, scene):
    """Evaluator-only initialization hook, applied independently by gateway and Runner."""
    subject = PolicySubject.model_validate(subject) if isinstance(subject, dict) else subject
    validate_base_catalog(catalog, subject)
    try:
        return apply_binding(catalog, subject, scene, {PolicyProgram.id: PolicyProgram})
    except LibraryError as exc:
        code = (
            "policy_subject_mismatch"
            if exc.detail["code"] == "subject_scene_mismatch"
            else "policy_binding_not_applied"
        )
        raise _invalid("Policy replacement or fixed Scene differs", code) from exc


def validate_policy_execution(subject, evidence, *, runtime=None):
    subject = PolicySubject.model_validate(subject) if isinstance(subject, dict) else subject
    try:
        value = PolicyExecution.model_validate(evidence)
        identity = subject.candidateIdentity
        if (
            value.policy != identity.id
            or value.policyContentHash != identity.contentHash
            or value.onnxSha256 != identity.onnxSha256
            or value.observationSha256 != identity.observationSha256
            or value.validOutputs > value.inferenceCalls
            or value.inferenceCalls > 0
            and value.actionStarts == 0
        ):
            raise ValueError("Actual native policy identity/counters differ")
        if runtime is not None and (
            runtime.get("available") is not True
            or value.runtimeVersion != runtime.get("runtimeVersion")
            or any(provider not in runtime.get("availableProviders", []) for provider in value.providers)
        ):
            raise ValueError("Actual policy runtime differs from the common native runtime probe")
        return value.model_dump(mode="json")
    except (ValueError, TypeError) as exc:
        raise _invalid(
            "Native policy execution evidence is unavailable or differs", "policy_execution_unavailable"
        ) from exc


def validate_policy_material(candidate, binding, roots, *, controller=None):
    identity = CandidateIdentity.model_validate(candidate)
    record, package = PolicyLibrary(roots).resolve(identity.id)
    if (
        binding.get("program") != PolicyProgram.id
        or binding.get("type") != PolicyProgram.type
        or binding.get("config", {}).get("policy") != identity.id
        or any(record.get(name) != getattr(identity, name)
               for name in CandidateIdentity.model_fields if name != "kind")
    ):
        raise _invalid("Deployed Policy material or binding differs", "policy_subject_changed")
    task = read_json(package / "task.json")
    if task["robot"] != binding.get("key") or task["implements"].rsplit(".", 1)[0] != PolicyProgram.type:
        raise _invalid("Deployed Policy Task target differs")
    if controller is not None and (
        controller.get("program") != PolicyProgram.id
        or controller.get("policy") != identity.id
        or controller.get("programArtifact") is not None
    ):
        raise _invalid("Deployed Controller/Policy material differs")
    return record
