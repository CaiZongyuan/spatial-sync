"""Common simulation binding facts. These functions do not authorize code execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from packages.info.declarations import EntityBindingDeclaration
from packages.library.common import LibraryError

if TYPE_CHECKING:
    from packages.info.catalog import Catalog
    from packages.info.program import Program
    from packages.library.scenes import SceneVersion


class SceneFacts(Protocol):
    id: str
    contentHash: str
    source: str


class TargetFacts(Protocol):
    entityKey: str
    interface: str
    action: str


class SimulationSubject(Protocol):
    candidateIdentity: object
    evaluationScene: SceneFacts
    target: TargetFacts
    baseBindings: Mapping[str, str]
    binding: dict[str, Any]
    bindingHash: str


Subject = TypeVar("Subject", bound=SimulationSubject)
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1)]


class SubjectSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, serialize_by_alias=True)


class EvaluationScene(SubjectSchema):
    id: Text
    contentHash: Hash
    source: Literal["newton"]


class BindingTarget(SubjectSchema):
    entityKey: Text
    interface: Text
    action: Text


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def _invalid(code, message):
    return LibraryError(code, message, 409)


def canonical_binding(binding, registry):
    """Match Catalog normalization without constructing any Program."""
    value = deepcopy(binding)
    program = registry[value["program"]]
    if value["type"] != program.type:
        raise ValueError("Declared Program/interface differ")
    value["config"] = program.Config.model_validate(value["config"]).model_dump(mode="json")
    return EntityBindingDeclaration.model_validate(value).model_dump(
        mode="json", by_alias=True, exclude_none=True, exclude={"alerts"}
    )


def fixed_bindings(scene, source, registry):
    """Read verified instantiated declarations shared by both simulation adapters."""
    scene.require_launchable()
    try:
        bindings = scene.report()["effectiveBindings"][source]
        if not bindings:
            raise ValueError("No verified effective bindings")
        result = {value["key"]: canonical_binding(value, registry) for value in bindings}
        if len(result) != len(bindings):
            raise ValueError("Duplicate effective binding")
        return result
    except (KeyError, TypeError, ValueError) as error:
        raise _invalid(
            "subject_binding_unavailable", "Verified evaluation bindings are unavailable"
        ) from error


def _require_target(subject):
    if (
        subject.target.entityKey not in subject.baseBindings
        or subject.binding.get("key") != subject.target.entityKey
        or subject.binding.get("type") != subject.target.interface
        or fingerprint(subject.binding) != subject.bindingHash
    ):
        raise _invalid("subject_changed", "Frozen target replacement facts differ")


def validate_base_catalog(catalog: Catalog, subject: SimulationSubject):
    """Require every original binding before any target replacement."""
    _require_target(subject)
    actual = {key: fingerprint(binding) for key, binding in catalog.entities.items()}
    if actual != subject.baseBindings:
        raise _invalid("subject_base_binding_changed", "Actual simulation base bindings differ")


def validate_applied_catalog(catalog: Catalog, subject: Subject) -> Subject:
    """Prove the one requested replacement was applied and all other bindings survived."""
    _require_target(subject)
    expected = dict(subject.baseBindings)
    expected[subject.target.entityKey] = subject.bindingHash
    actual = {key: fingerprint(binding) for key, binding in catalog.entities.items()}
    if actual != expected:
        raise _invalid("subject_binding_not_applied", "Actual target replacement is missing or differs")
    return subject


def apply_binding(
    catalog: Catalog, subject: SimulationSubject, scene: SceneVersion, registry: Mapping[str, type[Program]]
) -> Catalog:
    """Build only declarative Catalog data; never instantiate a Program or import Candidate code."""
    if (
        scene is None
        or scene.id != subject.evaluationScene.id
        or scene.content_hash != subject.evaluationScene.contentHash
    ):
        raise _invalid("subject_scene_mismatch", "Replacement belongs to another fixed Scene")
    validate_base_catalog(catalog, subject)
    program = registry.get(subject.binding["program"])
    if program is None or program.type != subject.target.interface:
        raise _invalid("subject_changed", "Replacement implementation is not registered for the interface")
    normalized = program.Config.model_validate(subject.binding["config"]).model_dump(mode="json")
    if fingerprint(normalized) != fingerprint(subject.binding["config"]):
        raise _invalid("subject_binding_not_applied", "Replacement configuration is not canonical")
    catalog.replace_program(subject.target.entityKey, program, normalized)
    validate_applied_catalog(catalog, subject)
    return catalog
