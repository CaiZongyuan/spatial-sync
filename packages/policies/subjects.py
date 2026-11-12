"""Freeze policy identity separately from the fixed evaluation world and its bindings."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.library.common import LibraryError, document
from packages.programs.subjects import (
    BindingTarget,
    EvaluationScene,
    apply_binding,
    fingerprint,
    validate_applied_catalog,
)
from packages.programs.subjects import canonical_binding as canonical_binding
from packages.programs.subjects import fixed_bindings as simulation_fixed_bindings
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


class Target(BindingTarget):
    interface: str = Field(pattern=r"^[a-z0-9-]+/v[1-9][0-9]*$")


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


def policy_programs():
    from packages.sim_runtime.info_catalog import REGISTRY

    return {name: cls for name, cls in REGISTRY.items() if getattr(cls, "policy_program", False)}


def policy_program_for(interface: str, base_program: str | None = None):
    programs = policy_programs()
    if base_program in programs and programs[base_program].type in (interface, None):
        return programs[base_program]
    exact = [cls for cls in programs.values() if cls.type == interface]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise _invalid("Ambiguous policy runtime Programs for interface")
    generic = [cls for cls in programs.values() if cls.type is None]
    if len(generic) == 1:
        return generic[0]
    raise _invalid("No policy runtime Program is registered for interface")


def fixed_bindings(scene, source):
    """Use the validated instantiated declarations; never guess a missing robot or document."""
    from packages.sim_runtime.info_catalog import REGISTRY

    try:
        return simulation_fixed_bindings(scene, source, REGISTRY)
    except LibraryError as exc:
        if exc.detail["code"] != "subject_binding_unavailable":
            raise
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
    ):
        raise _invalid("Policy Task and fixed evaluation target differ")
    bindings = fixed_bindings(scene, "newton")
    base = bindings.get(task["robot"])
    if base is None or base["type"] != interface:
        raise _invalid("Policy target is absent or has a different interface")
    program_cls = policy_program_for(interface, base.get("program"))
    try:
        config = program_cls.apply_policy_override(base["config"], action, policy["id"])
    except (ValidationError, ValueError) as exc:
        raise _invalid("Policy binding configuration is invalid for target Program") from exc
    spec = ObservationSpec.model_validate(document(package / "obs_spec.yaml"))
    allowed = program_cls.policy_joints(config)
    units = [allowed.get(key) for key in spec.action.jointKeys]
    if not set(spec.action.jointKeys) <= set(allowed) or spec.action.units != units:
        raise _invalid(
            "Policy action channels do not match the fixed robot binding", "policy_action_binding_mismatch"
        )
    replacement = base | {"program": program_cls.id, "config": config}
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
    program_cls = policy_programs().get(subject.binding.get("program"))
    if (
        program_cls is None
        or program_cls.type not in (subject.target.interface, None)
        or subject.binding.get("key") != subject.target.entityKey
        or subject.binding.get("type") != subject.target.interface
        or program_cls.bound_policy_for_action(
            subject.binding.get("config", {}), subject.target.action
        )
        != subject.candidateIdentity.id
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
        return apply_binding(catalog, subject, scene, policy_programs())
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
    library = PolicyLibrary(roots)
    record, package = library.resolve(identity.id)
    program_cls = policy_programs().get(binding.get("program"))
    task = read_json(package / "task.json")
    interface, action = task["implements"].rsplit(".", 1)
    config = binding.get("config", {})
    if (
        program_cls is None
        or program_cls.type not in (binding.get("type"), None)
        or program_cls.bound_policy_for_action(config, action) != identity.id
        or any(
            record.get(name) != getattr(identity, name)
            for name in CandidateIdentity.model_fields
            if name != "kind"
        )
    ):
        raise _invalid("Deployed Policy material or binding differs", "policy_subject_changed")
    if task["robot"] != binding.get("key") or interface != binding.get("type"):
        raise _invalid("Deployed Policy Task target differs")
    for sibling_action, sibling_id in (config.get("policies") or {}).items():
        _, sibling_pkg = library.resolve(sibling_id)
        sibling_task = read_json(sibling_pkg / "task.json")
        if (
            sibling_task["robot"] != binding.get("key")
            or sibling_task["implements"] != f"{binding.get('type')}.{sibling_action}"
        ):
            raise _invalid("Deployed sibling Policy Task target differs", "policy_subject_changed")
    if controller is not None and (
        controller.get("program") != program_cls.id
        or controller.get("policy") != identity.id
        or controller.get("programArtifact") is not None
    ):
        raise _invalid("Deployed Controller/Policy material differs")
    return record
