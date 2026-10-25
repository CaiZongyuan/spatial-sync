"""Real library publication survives interruption and refuses a competing current."""

from contextlib import nullcontext
from copy import deepcopy

import pytest

from apps.api.app.library_validation import program_schemas
from packages.library.common import document
from packages.library.imports import LibraryImports
from packages.library.transactions import durable_json
from packages.library.validation import StructuralValidator
from packages.policies.subjects import resolve_policy_subject
from packages.training.deployment_publication import DeploymentPlan, DeploymentPublication
from tests.unit.test_policy_subjects import policy_client, policy_inputs  # noqa: F401


class ControlledPublicationValidator(StructuralValidator):
    """Transaction fixture only: real structural checks, explicitly unproved physics."""

    def run(self, *args, **kwargs):
        report = self.run_structure(*args, **kwargs)
        report["checks"].append({
            "id": "P2", "tier": "physics", "status": "warn", "notApplicable": True,
            "message": "Controlled fixture: physical stability and learned quality are not asserted",
        })
        return report


@pytest.fixture
def publication_inputs(policy_inputs, policy_client, tmp_path):  # noqa: F811
    manager, policy, scene, suite, _ = policy_inputs
    subject = resolve_policy_subject(manager, policy["id"], suite, scene)
    resources = policy_client.app.state.robot_resources
    model = deepcopy(resources.model("fr3-taught-motion"))
    for key in ("root", "contentHash"):
        model.pop(key)
    model.update(id="controlled-policy", title="Controlled publication fixture", family="rl-policy",
                 actionSpace="joint_position_delta")
    model["capability"]["program"] = "robot_arm.policy"
    user = manager.library_assets.roots["user"]
    durable_json(user / "robot-models/controlled-policy.yaml", model)
    importer = LibraryImports(
        manager.library_assets, manager.library,
        ControlledPublicationValidator(tmp_path / "validation", program_schemas)
    )
    plan = DeploymentPlan(
        promotion_id="promotion-controlled", scene=scene, subject=subject,
        controller=resources.controller("fr3-arm"), context_hash="controlled-trusted-context",
        version_fields={"embodiment": "fr3-sim", "robotModel": "controlled-policy", "policy": policy["id"],
                        "program": "robot_arm.policy",
                        "action": {"space": "joint_position_delta", "hz": 20.0},
                        "observation": policy["observationSha256"], "task": policy["task"],
                        "evaluationId": "eval-controlled-train"},
    )
    original = {name: (scene.path / name).read_bytes() for name in scene.files}
    return manager, resources, importer, plan, original, tmp_path / "publication"


def make_publication(inputs, **options):
    manager, resources, importer, _, _, state = inputs
    return DeploymentPublication(manager.library_assets, manager.library, resources, importer, state,
                                 validate_context=lambda _: None, commit_context=lambda _: nullcontext(),
                                 **options)


def test_scene_published_before_controller_failure_resumes_once_and_preserves_old_bytes(publication_inputs):
    manager, resources, _, plan, original, _ = publication_inputs

    def fail(effect, _):
        if effect == "scene_published":
            raise OSError("Controlled interruption before Controller publication")

    first = make_publication(publication_inputs, after_effect=fail)
    first.prepare(plan)
    with pytest.raises(OSError, match="Controlled interruption"):
        first.publish(plan.promotion_id)
    interrupted = first.read(plan.promotion_id)
    assert interrupted["phase"] == "scene_published"
    assert resources.controller("fr3-arm")["currentVersion"] is None
    published = interrupted["scene"]["id"]
    restarted = make_publication(publication_inputs)
    completed = restarted.publish(plan.promotion_id)
    assert completed["phase"] == "committed" and completed["scene"]["id"] == published
    assert resources.controller("fr3-arm")["current"]["scene"] == published
    assert restarted.publish(plan.promotion_id) == completed
    assert len([scene for scene in manager.library.versions.values() if scene.root == "user"]) == 1
    assert all((plan.scene.path / name).read_bytes() == value for name, value in original.items())


def test_competing_current_between_scene_and_controller_is_preserved_and_old_intent_blocked(
    publication_inputs,
):
    manager, resources, _, plan, _, _ = publication_inputs
    user = manager.library_assets.roots["user"]

    def compete(effect, _):
        if effect != "scene_published":
            return
        current = {key: plan.controller[key] for key in ("id", "title", "embodiment", "currentVersion")}
        version = deepcopy(plan.version_fields) | {
            "controller": "fr3-arm", "version": 99, "scene": plan.scene.id,
            "program": "robot_arm.newton", "policy": None, "robotModel": "fr3-taught-motion",
        }
        durable_json(user / "controllers/fr3-arm/99.yaml", version)
        durable_json(user / "controllers/fr3-arm/controller.yaml", current | {"currentVersion": 99})

    publication = make_publication(publication_inputs, after_effect=compete)
    publication.prepare(plan)
    blocked = publication.publish(plan.promotion_id)
    assert blocked["phase"] == "blocked" and blocked["error"]["code"] == "controller_current_changed"
    assert resources.controller("fr3-arm")["currentVersion"] == 99
    assert not (user / "controllers/fr3-arm/1.yaml").exists()
    assert publication.publish(plan.promotion_id) == blocked
    assert document(user / "controllers/fr3-arm/controller.yaml")["currentVersion"] == 99
