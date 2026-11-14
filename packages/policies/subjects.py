"""Freeze policy identity separately from the fixed evaluation world and its bindings."""

import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.info.program import program_supports_type
from packages.library.common import LibraryError, document
from packages.library.python_stages import resources as python_resources
from packages.library.structural import USDGraph, usd_resources
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

from .library import PolicyLibrary, digest, read_json
from .observations import (
    ObservationSpec,
    observation_canonical_refs,
    spec_within_robot_entity,
)

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
    if base_program in programs and program_supports_type(programs[base_program], interface):
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


def scene_stage_resources(scene) -> dict:
    """Inspect physical joint, body, and site resources from the Scene's stage."""
    payload = scene.runtime_payload()
    metadata = payload["metadata"]
    if metadata["stage"].startswith("python:"):
        resources = python_resources(metadata, payload["package"], payload["bindings"])
        if "usdPath" in resources:
            path = Path(resources["usdPath"])
            scope = {
                "path": str(path.parent),
                "files": {p.name: str(p) for p in path.parent.iterdir() if p.is_file()},
            }
            resources = usd_resources(USDGraph(scope, []).compose(path.name))
    else:
        graph = USDGraph(payload["package"], payload["assets"], payload["expectedAssets"])
        stage = graph.compose(metadata["stage"])
        if stage is None or graph.errors:
            raise ValueError("; ".join(graph.errors) or "USD stage cannot be composed")
        resources = usd_resources(stage)
    return resources


def scene_joint_resources(scene) -> dict[str, dict]:
    """Inspect physical joint kinds and finite limits from the Scene's stage."""
    return scene_stage_resources(scene).get("joints", {})


def _validate_scene_action_channels(scene, program_cls, config, spec):
    configured = program_cls.policy_joints(config)
    try:
        resources = scene_stage_resources(scene)
        stage_joints = resources.get("joints", {})
        stage_bodies = set(resources.get("bodies", ()))
        stage_sites = set(resources.get("sites", ()))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise _invalid(
            "Scene physical joint resources are unavailable", "policy_binding_unavailable"
        ) from exc
    actual_units: dict[str, str] = {}
    actual_limits: dict[str, tuple[float | None, float | None]] = {}
    for key in configured:
        info = stage_joints.get(key)
        if info is None or info.get("kind") not in ("revolute", "prismatic"):
            raise _invalid(
                "Policy action channels do not match the fixed robot binding",
                "policy_action_binding_mismatch",
            )
        actual_units[key] = "m" if info["kind"] == "prismatic" else "rad"
        bounds = info.get("limits")
        lo = float(bounds[0]) if bounds is not None and math.isfinite(bounds[0]) else None
        hi = float(bounds[1]) if bounds is not None and math.isfinite(bounds[1]) else None
        actual_limits[key] = (lo, hi)
    prismatic_cfg = config.get("prismaticJointKeys")
    if prismatic_cfg:
        actual_prismatic = {key for key, unit in actual_units.items() if unit == "m"}
        if set(prismatic_cfg) != actual_prismatic:
            raise _invalid(
                "prismaticJointKeys does not match actual physical joint kinds",
                "policy_action_binding_mismatch",
            )
    units = [actual_units.get(key) for key in spec.action.jointKeys]
    if not set(spec.action.jointKeys) <= set(actual_units) or spec.action.units != units:
        raise _invalid(
            "Policy action channels do not match the fixed robot binding", "policy_action_binding_mismatch"
        )
    for key, act_lo, act_hi in zip(
        spec.action.jointKeys, spec.action.lower, spec.action.upper, strict=True
    ):
        phys_lo, phys_hi = actual_limits[key]
        if (phys_lo is not None and act_lo < phys_lo - 1e-6) or (
            phys_hi is not None and act_hi > phys_hi + 1e-6
        ):
            raise _invalid(
                f"Policy action bounds exceed physical joint limits on {key}",
                "policy_action_binding_mismatch",
            )
    for item in spec.observations:
        role, keys, frame_body = observation_canonical_refs(item)
        if role == "joint":
            for key in keys:
                info = stage_joints.get(key)
                if info is None or info.get("kind") not in ("revolute", "prismatic"):
                    raise _invalid(
                        f"Policy observation joint {key} does not match target Scene joints",
                        "policy_observation_binding_mismatch",
                    )
        elif role == "body":
            for key in keys:
                if key not in stage_bodies:
                    raise _invalid(
                        f"Policy observation body {key} does not exist in target Scene",
                        "policy_observation_binding_mismatch",
                    )
        elif role == "site":
            for key in keys:
                if key not in stage_sites:
                    raise _invalid(
                        f"Policy observation site {key} does not exist in target Scene",
                        "policy_observation_binding_mismatch",
                    )
        if frame_body is not None and frame_body not in stage_bodies:
            raise _invalid(
                f"Policy observation frame body {frame_body} does not exist in target Scene",
                "policy_observation_binding_mismatch",
            )


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
    _validate_scene_action_channels(scene, program_cls, config, spec)
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
        not program_supports_type(program_cls, subject.target.interface)
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


def validate_policy_material(
    candidate,
    binding,
    roots,
    *,
    controller=None,
    expected_action=None,
    allow_entity_remap: bool = False,
):
    identity = CandidateIdentity.model_validate(candidate)
    library = PolicyLibrary(roots)
    record, package = library.resolve(identity.id)
    program_cls = policy_programs().get(binding.get("program"))
    task = read_json(package / "task.json")
    interface, action = task["implements"].rsplit(".", 1)
    config = binding.get("config", {})
    if (
        not program_supports_type(program_cls, binding.get("type"))
        or (expected_action is not None and action != expected_action)
        or program_cls.bound_policy_for_action(config, action) != identity.id
        or digest(package / record["onnx"]) != identity.onnxSha256
        or digest(package / "obs_spec.yaml") != identity.observationSha256
        or any(
            record.get(name) != getattr(identity, name)
            for name in CandidateIdentity.model_fields
            if name != "kind"
        )
    ):
        raise _invalid("Deployed Policy material or binding differs", "policy_subject_changed")
    target_key = binding.get("key")
    if interface != binding.get("type") or not isinstance(target_key, str) or not target_key.startswith("/"):
        raise _invalid("Deployed Policy Task target differs")
    if task["robot"] != target_key:
        if not allow_entity_remap:
            raise _invalid("Deployed Policy Task target differs")
        spec = ObservationSpec.model_validate(document(package / "obs_spec.yaml"))
        if not spec_within_robot_entity(spec, task["robot"]):
            raise _invalid("Deployed Policy channels cannot remap outside Task robot")
    if controller is not None and (
        controller.get("program") != program_cls.id
        or controller.get("policy") != identity.id
        or controller.get("programArtifact") is not None
    ):
        raise _invalid("Deployed Controller/Policy material differs")
    return record


def validate_policy_bundle_material(
    bundle,
    binding,
    roots,
    *,
    controller=None,
    allow_entity_remap: bool = False,
):
    config = binding.get("config", {})
    config_policies = config.get("policies")
    if (
        not isinstance(bundle, dict)
        or bundle.get("kind") != "policy-bundle"
        or not isinstance(bundle.get("policies"), dict)
        or not bundle["policies"]
        or not isinstance(config_policies, dict)
        or set(bundle["policies"]) != set(config_policies)
    ):
        raise _invalid("Deployed multi-policy material bundle differs", "policy_subject_changed")
    program_cls = policy_programs().get(binding.get("program"))
    if not program_supports_type(program_cls, binding.get("type")):
        raise _invalid("Deployed Policy Program does not support entity type", "policy_subject_changed")
    library = PolicyLibrary(roots)
    records = {}
    for action, act_candidate in bundle["policies"].items():
        records[action] = validate_policy_material(
            act_candidate,
            binding,
            roots,
            expected_action=action,
            allow_entity_remap=allow_entity_remap,
        )
    if controller is not None:
        ctrl_policies = controller.get("policies")
        if (
            controller.get("program") != program_cls.id
            or controller.get("programArtifact") is not None
            or not isinstance(ctrl_policies, dict)
            or set(ctrl_policies) != set(config_policies)
            or controller.get("policy") not in set(config_policies.values())
        ):
            raise _invalid("Deployed multi-policy Controller composition differs", "policy_subject_changed")
        for action, comp in ctrl_policies.items():
            act_identity = CandidateIdentity.model_validate(bundle["policies"][action])
            _, pkg = library.resolve(act_identity.id)
            spec = ObservationSpec.model_validate(document(pkg / "obs_spec.yaml"))
            task = read_json(pkg / "task.json")
            act_doc = comp.get("action") or {}
            if (
                comp.get("policy") != act_identity.id
                or comp.get("observation") != act_identity.observationSha256
                or comp.get("task") != act_identity.task
                or act_doc.get("space") != spec.action.space
                or act_doc.get("hz") != task["control"]["hz"]
            ):
                raise _invalid(
                    f"ControllerVersion.policies[{action}] differs from committed Policy material",
                    "policy_subject_changed",
                )
    return records
