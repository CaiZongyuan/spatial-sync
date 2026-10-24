"""Actual ordinary validation still refuses the known unstable bundled initial state."""

from contextlib import nullcontext

import pytest

from apps.api.app.library_validation import program_schemas
from packages.library.imports import LibraryImports
from packages.library.validation import StructuralValidator
from packages.training.deployment_publication import DeploymentPublication
from tests.unit.test_controller_publication import publication_inputs  # noqa: F401
from tests.unit.test_policy_subjects import policy_client, policy_inputs  # noqa: F401

pytest.importorskip("newton")
pytestmark = pytest.mark.newton


def test_actual_full_validation_refuses_before_controller_commit(publication_inputs, tmp_path):  # noqa: F811
    manager, resources, _, plan, original, state = publication_inputs
    real = LibraryImports(manager.library_assets, manager.library,
                          StructuralValidator(tmp_path / "actual-validation", program_schemas))
    publication = DeploymentPublication(
        manager.library_assets, manager.library, resources, real, state,
        validate_context=lambda _: None, commit_context=lambda _: nullcontext(),
    )
    publication.prepare(plan)
    result = publication.publish(plan.promotion_id)
    assert result["phase"] == "blocked" and result["error"]["code"] == "scene_not_launchable"
    report = manager.library.resolve(result["scene"]["id"]).report()
    failed = [row for row in report["checks"] if row["id"] == "P2" and row["status"] == "fail"]
    assert failed and all(
        row["measurements"]["displacementM"] > row["measurements"]["limitM"] for row in failed
    )
    assert resources.controller("fr3-arm")["currentVersion"] is None
    assert not (manager.library_assets.roots["user"] / "controllers/fr3-arm/1.yaml").exists()
    assert all((plan.scene.path / name).read_bytes() == value for name, value in original.items())
