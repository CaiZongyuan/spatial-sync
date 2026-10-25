"""One private simulation descriptor path; material/authorization stay in concrete adapters."""

from dataclasses import dataclass
from typing import Callable

from packages.library.common import LibraryError
from packages.policies import subjects as policies


@dataclass(frozen=True)
class _Adapter:
    model: type
    verify: Callable
    apply: Callable
    catalog: Callable
    execution: Callable
    material: Callable


_POLICY = _Adapter(
    policies.PolicySubject,
    policies.verify_policy_subject,
    policies.apply_policy_catalog,
    policies.validate_policy_catalog,
    policies.validate_policy_execution,
    policies.validate_policy_material,
)


def _script_adapter():
    from packages.programs import script_subjects as scripts

    return _Adapter(
        scripts.ScriptSubject,
        scripts.verify_program_subject,
        scripts.apply_script_catalog,
        scripts.validate_script_catalog,
        scripts.validate_program_execution,
        scripts.validate_program_material,
    )


def _adapter(value):
    schema = value.get("schema") if isinstance(value, dict) else getattr(value, "schema_version", None)
    if schema == "lw.script-subject/1":
        return _script_adapter()
    if schema == "lw.policy-subject/1":
        return _POLICY
    raise LibraryError("simulation_subject_invalid", "Unknown simulation Subject schema", 422)


def parse_subject(value):
    adapter = _adapter(value)
    return adapter.model.model_validate(value) if isinstance(value, dict) else value


def select_subject(*, simulation_subject=None, policy_subject=None):
    current = parse_subject(simulation_subject) if simulation_subject is not None else None
    legacy = parse_subject(policy_subject) if policy_subject is not None else None
    if legacy is not None and not isinstance(legacy, policies.PolicySubject):
        raise LibraryError("simulation_subject_invalid", "Legacy policy descriptor requires a policy", 422)
    if current is not None and legacy is not None and current.document() != legacy.document():
        raise LibraryError("simulation_subject_changed", "Private simulation descriptors differ", 409)
    return current or legacy


def admitted_subject(record):
    return select_subject(
        simulation_subject=record.get("simulationSubject"), policy_subject=record.get("policySubject")
    )


def scene_requires_material_authorization(scene, source):
    programs = {policies.PolicyProgram.id, "robot_arm.script"}
    # Legacy valid builtin reports predate effectiveBindings. Inspect their resolved
    # declarations too, so neither a missing report key nor a template bypasses admission.
    bindings = scene.report().get("effectiveBindings", {}).get(source)
    if bindings is None:
        bindings = [item.binding for item in scene.declarations(source).declarations]
    return any(binding["program"] in programs for binding in bindings)


def material_bindings(catalog):
    return {
        key: binding
        for key, binding in catalog.entities.items()
        if binding["program"] in (policies.PolicyProgram.id, "robot_arm.script")
    }


def validate_candidate_material(candidate, binding, roots, *, controller=None):
    schema = {"policy": "lw.policy-subject/1", "program": "lw.script-subject/1"}.get(candidate.get("kind"))
    return _adapter({"schema": schema}).material(candidate, binding, roots, controller=controller)


def resolve_simulation_subject(manager, requested, suite, scene, *, plan_id=None, plan_revision=None):
    if requested.startswith("program/"):
        from packages.programs import script_subjects as scripts

        return scripts.resolve_program_subject(
            manager, requested, suite, scene, plan_id=plan_id, plan_revision=plan_revision
        )
    return policies.resolve_policy_subject(manager, requested, suite, scene)


def verify_simulation_subject(manager, subject, suite, scene):
    return _adapter(subject).verify(manager, parse_subject(subject), suite, scene)


def apply_simulation_catalog(catalog, subject, scene):
    return _adapter(subject).apply(catalog, parse_subject(subject), scene)


def validate_simulation_catalog(catalog, subject):
    return _adapter(subject).catalog(catalog, parse_subject(subject))


def validate_simulation_execution(subject, evidence, *, runtime=None):
    return _adapter(subject).execution(parse_subject(subject), evidence, runtime=runtime)
