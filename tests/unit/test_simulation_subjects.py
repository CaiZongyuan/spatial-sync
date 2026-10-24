"""Shared binding facts change no non-target behavior and execute no program code."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from packages.info.catalog import Catalog
from packages.info.program import ProgramConfig
from packages.library.common import LibraryError
from packages.programs.subjects import (
    apply_binding,
    fingerprint,
    validate_applied_catalog,
    validate_base_catalog,
)


def inputs():
    created = []

    class Replacement:
        id, type = "replacement", "fixture/v1"

        class Config(ProgramConfig):
            amount: int = 3

        def __init__(self):
            created.append(True)

    entities = {
        "/World/Target": {"key": "/World/Target", "type": "fixture/v1", "program": "base", "config": {}},
        "/World/Other": {"key": "/World/Other", "type": "fixture/v1", "program": "other", "config": {}},
    }
    catalog = Catalog(sceneRevision="scene", entities=deepcopy(entities))
    binding = entities["/World/Target"] | {"program": "replacement", "config": {"amount": 3}}
    subject = SimpleNamespace(
        candidateIdentity={"kind": "program", "id": "fixture", "contentHash": "artifact"},
        evaluationScene=SimpleNamespace(id="scene@1", contentHash="fixed-scene", source="newton"),
        target=SimpleNamespace(entityKey="/World/Target", interface="fixture/v1", action="run"),
        baseBindings={key: fingerprint(value) for key, value in entities.items()},
        binding=binding,
        bindingHash=fingerprint(binding),
    )
    scene = SimpleNamespace(id="scene@1", content_hash="fixed-scene")
    return catalog, subject, scene, {Replacement.id: Replacement}, entities, created


def test_shared_binding_metadata_preserves_non_target_and_never_constructs_programs():
    catalog, subject, scene, registry, original, created = inputs()
    validate_base_catalog(catalog, subject)
    assert apply_binding(catalog, subject, scene, registry) is catalog
    assert validate_applied_catalog(catalog, subject) is subject
    assert catalog.entities["/World/Other"] == original["/World/Other"]
    assert catalog.entities["/World/Target"] == subject.binding
    assert created == []


def test_non_target_or_scene_drift_and_missing_override_are_rejected():
    catalog, subject, scene, registry, _, created = inputs()
    with pytest.raises(LibraryError):
        validate_applied_catalog(catalog, subject)
    catalog.entities["/World/Other"]["config"]["changed"] = True
    with pytest.raises(LibraryError):
        validate_base_catalog(catalog, subject)
    catalog, subject, _, registry, _, _ = inputs()
    scene.content_hash = "changed-scene"
    with pytest.raises(LibraryError):
        apply_binding(catalog, subject, scene, registry)
    assert created == []


def test_binding_defaults_cannot_silently_change_the_frozen_replacement():
    catalog, subject, scene, registry, original, created = inputs()
    subject.binding["config"] = {}
    subject.bindingHash = fingerprint(subject.binding)
    with pytest.raises(LibraryError) as mismatch:
        apply_binding(catalog, subject, scene, registry)
    assert mismatch.value.detail["code"] == "subject_binding_not_applied"
    assert catalog.entities == original and created == []
