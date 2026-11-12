"""De-armified policy subject resolution, multi-policy override and Controller promotion authority."""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.library_validation import program_schemas
from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.catalog import compile_catalog
from packages.library.common import LibraryError, content_hash, load_entity_types
from packages.library.imports import LibraryImports
from packages.library.transactions import durable_json
from packages.policies.subjects import (
    apply_policy_catalog,
    resolve_policy_subject,
    validate_base_catalog,
    validate_policy_catalog,
    validate_policy_material,
)
from packages.programs.deployment import verify_runtime_deployment
from packages.programs.embodied import EmbodiedPolicyProgram, EmbodiedPostureProgram
from packages.programs.simulation import scene_requires_material_authorization
from packages.programs.subjects import fingerprint
from packages.protocol.schema import Actor
from packages.training.deployment_publication import (
    DeploymentPlan,
    DeploymentPublication,
    read_committed_permit,
)
from tests.gateway.test_training_jobs import TOKEN
from tests.unit.test_controller_publication import ControlledPublicationValidator
from tests.unit.test_multi_policy_program import BALANCE_BOT_TYPE, linear_onnx_bytes, write_policy_package


def _write_custom_scene_and_resources(user_lib, *, binding_program, binding_config):
    (user_lib / "types").mkdir(parents=True, exist_ok=True)
    (user_lib / "types/balance-bot.v1.yaml").write_text(
        yaml.safe_dump(BALANCE_BOT_TYPE, sort_keys=False), encoding="utf-8"
    )

    # Minimal valid USD asset & scene on disk in user library
    asset_dir = user_lib / "assets/robot/mini-balancer/1"
    asset_dir.mkdir(parents=True, exist_ok=True)
    usda_asset = """#usda 1.0
(
    defaultPrim = "Asset"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "Asset"
{
    def Cube "Base" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"]
    )
    {
        double size = 0.2
        float physics:mass = 1.0
        float3 physics:diagonalInertia = (0.01, 0.01, 0.01)
    }
    def Cube "LeftWheel" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"]
    )
    {
        double size = 0.08
        double3 xformOp:translate = (0, 0.12, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        float physics:mass = 0.2
        float3 physics:diagonalInertia = (0.001, 0.001, 0.001)
    }
    def Cube "RightWheel" (
        prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"]
    )
    {
        double size = 0.08
        double3 xformOp:translate = (0, -0.12, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        float physics:mass = 0.2
        float3 physics:diagonalInertia = (0.001, 0.001, 0.001)
    }
    def PhysicsRevoluteJoint "j1"
    {
        uniform token physics:axis = "Y"
        rel physics:body0 = </Asset/Base>
        rel physics:body1 = </Asset/LeftWheel>
        float physics:lowerLimit = -90
        float physics:upperLimit = 90
    }
    def PhysicsRevoluteJoint "j2"
    {
        uniform token physics:axis = "Y"
        rel physics:body0 = </Asset/Base>
        rel physics:body1 = </Asset/RightWheel>
        float physics:lowerLimit = -90
        float physics:upperLimit = 90
    }
}
"""
    asset_bytes = usda_asset.encode("utf-8")
    (asset_dir / "asset.usda").write_bytes(asset_bytes)
    (asset_dir / "LICENSE.txt").write_text("MIT\n", encoding="utf-8")
    import hashlib

    durable_json(
        asset_dir / "provenance.json",
        {
            "source": "synthetic-test-fixture",
            "files": {
                "asset.usda": {
                    "sha256": hashlib.sha256(asset_bytes).hexdigest(),
                    "bytes": len(asset_bytes),
                }
            },
        },
    )
    (asset_dir / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "robot/mini-balancer",
                "title": "Mini Balancer",
                "kind": "robot",
                "stage": "asset.usda",
                "implements": [
                    {
                        "type": "balance-bot/v1",
                        "programs": {"newton": EmbodiedPostureProgram.id},
                        "config": {
                            "jointKeys": ["j1", "j2"],
                            "baseBody": "Base",
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    scene_dir = user_lib / "scenes/bot-arena/1"
    scene_dir.mkdir(parents=True, exist_ok=True)
    usda_scene = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "World"
{
    def PhysicsScene "PhysicsScene"
    {
    }
    def Xform "Lab"
    {
        def Xform "Bot" (
            prepend references = @library:assets/robot/mini-balancer/1/asset.usda@
        )
        {
            double3 xformOp:translate = (0, 0, 0.2)
            uniform token[] xformOpOrder = ["xformOp:translate"]
        }
    }
}
"""
    (scene_dir / "scene.usda").write_text(usda_scene, encoding="utf-8")
    (scene_dir / "scene.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "bot-arena",
                "title": "Bot Arena",
                "stage": "scene.usda",
                "sources": ["newton"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (scene_dir / "locations.yaml").write_text(
        yaml.safe_dump({"locations": {}}, sort_keys=False),
        encoding="utf-8",
    )
    entity_binding = {
        "key": "/World/Lab/Bot",
        "type": "balance-bot/v1",
        "program": binding_program,
        "config": binding_config,
    }
    (scene_dir / "devices.yaml").write_text(
        yaml.safe_dump({"scene": "bot-arena", "entities": [entity_binding]}, sort_keys=False),
        encoding="utf-8",
    )
    return asset_dir, scene_dir, entity_binding


def test_user_library_entity_types_extend_builtin_types_and_forbid_duplicates(tmp_path):
    user_lib = tmp_path / "library"
    (user_lib / "types").mkdir(parents=True)
    (user_lib / "types/balance-bot.v1.yaml").write_text(yaml.safe_dump(BALANCE_BOT_TYPE), encoding="utf-8")
    types = load_entity_types(user_lib)
    assert "balance-bot/v1" in types and "robot-arm/v1" in types

    # Shadowing a builtin type is strictly forbidden
    dup = deepcopy(BALANCE_BOT_TYPE) | {"type": "robot-arm/v1"}
    (user_lib / "types/collide.yaml").write_text(yaml.safe_dump(dup), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate entity type"):
        load_entity_types(user_lib)


def test_custom_multi_policy_subject_replaces_single_action_and_publishes_offset_controller(
    tmp_path, monkeypatch
):
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))

    joints = ["/World/Lab/Bot/j1", "/World/Lab/Bot/j2"]
    base = "/World/Lab/Bot/Base"

    balance_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "gravity",
                "source": "projected_gravity",
                "key": base,
                "frame": "body",
                "unit": "1",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            }
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad", "rad"],
            "defaultPosition": [0.0, 0.0],
            "scale": [0.2, 0.2],
            "lower": [-0.8, -0.8],
            "upper": [0.8, 0.8],
        },
    }
    balance_task = {
        "name": "balance-bot",
        "title": "Balance",
        "scene": "bot-arena@1",
        "robot": "/World/Lab/Bot",
        "implements": "balance-bot/v1.balance",
        "episode": {"params": {}},
        "success": {"property": "tiltDeg", "lte": 8.0},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    balance_id = write_policy_package(
        user_lib,
        name="balance-bot",
        rev=1,
        task=balance_task,
        spec=balance_spec,
        onnx_bytes=linear_onnx_bytes(np.zeros((3, 2), dtype=np.float32)),
    )

    move_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "cmd",
                "source": "command",
                "frame": "body",
                "unit": "m/s",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            }
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad", "rad"],
            "defaultPosition": [0.05, -0.05],
            "scale": [0.3, 0.3],
            "lower": [-0.8, -0.8],
            "upper": [0.8, 0.8],
        },
    }
    move_task = {
        "name": "move-bot",
        "title": "Move",
        "scene": "bot-arena@1",
        "robot": "/World/Lab/Bot",
        "implements": "balance-bot/v1.moveTo",
        "episode": {
            "params": {"speed": 0.15},
            "paramBounds": {"x": [-1.0, 1.0], "y": [-1.0, 1.0], "speed": [0.05, 0.25]},
        },
        "success": {"property": "distanceToGoal", "lte": 0.05},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    move_v1 = write_policy_package(
        user_lib,
        name="move-bot",
        rev=1,
        task=move_task,
        spec=move_spec,
        onnx_bytes=linear_onnx_bytes(np.zeros((3, 2), dtype=np.float32)),
    )
    move_v2 = write_policy_package(
        user_lib,
        name="move-bot",
        rev=2,
        task=move_task,
        spec=move_spec,
        onnx_bytes=linear_onnx_bytes(np.ones((3, 2), dtype=np.float32) * 0.1),
    )

    base_cfg = {
        "jointKeys": joints,
        "baseBody": base,
        "policies": {"balance": balance_id, "moveTo": move_v1},
        "commandMode": "planar_target",
        "maxSeconds": 10.0,
    }
    asset_dir, scene_dir, _ = _write_custom_scene_and_resources(
        user_lib, binding_program=EmbodiedPolicyProgram.id, binding_config=base_cfg
    )

    validator = ControlledPublicationValidator(state_root / "val", program_schemas)
    from packages.library.assets import AssetLibrary
    from packages.library.suites import SuiteLibrary

    pre_assets = AssetLibrary(state_root, user_root=user_lib)
    pre_assets.startup()
    asset_v = pre_assets.versions["robot/mini-balancer@1"]
    asset_report = validator.run(
        asset_dir, "asset", "asset:robot/mini-balancer@1", asset_v.metadata, pre_assets
    )
    durable_json(asset_dir / "validation.json", asset_report)
    pre_assets = AssetLibrary(state_root, user_root=user_lib)
    pre_assets.startup()

    scene_meta = yaml.safe_load((scene_dir / "scene.yaml").read_text(encoding="utf-8"))
    scene_binds = yaml.safe_load((scene_dir / "devices.yaml").read_text(encoding="utf-8"))
    scene_report = validator.run(
        scene_dir,
        "scene",
        "scene:bot-arena@1",
        scene_meta,
        pre_assets,
        bindings=scene_binds,
        expected_assets=[{"id": "robot/mini-balancer@1", "contentHash": content_hash(asset_dir)}],
    )
    assert scene_report["launchable"], scene_report
    durable_json(scene_dir / "validation.json", scene_report)

    settings = Settings(state_root=state_root, recordings_root=tmp_path / "rec", node_token=TOKEN)
    app = create_app(settings, controller_publication_validator=validator)
    with TestClient(app) as client:
        manager = client.app.state.manager
        scene = manager.library.resolve("bot-arena@1", launch=True, source="newton")

        # Update policy packages' sceneContentHash so provenance matches the real scene hash
        for name, rev in (("balance-bot", 1), ("move-bot", 1), ("move-bot", 2)):
            p_dir = user_lib / "policies" / name / str(rev)
            pkg = p_dir / "package"
            prov = yaml.safe_load((pkg / "policy.json").read_text(encoding="utf-8"))
            prov["sceneContentHash"] = scene.content_hash
            durable_json(pkg / "policy.json", prov)
            meta = yaml.safe_load((p_dir / "metadata.json").read_text(encoding="utf-8"))
            meta["contentHash"] = content_hash(pkg)
            durable_json(p_dir / "metadata.json", meta)

        # Create Evaluation Suite for moveTo
        suite_dir = user_lib / "suites/bot-move-suite/1"
        (suite_dir / "private").mkdir(parents=True)
        (suite_dir / "task.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "bot-move-suite",
                    "scene": scene.id,
                    "robot": "/World/Lab/Bot",
                    "implements": "balance-bot/v1.moveTo",
                    "timeoutSeconds": 5.0,
                    "success": {"entity": "/World/Lab/Bot", "property": "distanceToGoal", "lte": 0.05},
                    "milestones": [
                        {
                            "id": "arrive",
                            "when": {"entity": "/World/Lab/Bot", "property": "distanceToGoal", "lte": 0.05},
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (suite_dir / "private/seeds.yaml").write_text(
            yaml.safe_dump({"train-1": 1, "test-1": 2}, sort_keys=False),
            encoding="utf-8",
        )
        (suite_dir / "suite.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "suite/bot-move-suite",
                    "title": "Bot move suite",
                    "layer": "embodied",
                    "source": "newton",
                    "scene": scene.id,
                    "sceneContentHash": scene.content_hash,
                    "episodes": [
                        {"id": "train-1", "split": "train", "params": {"x": 0.4, "y": 0.0}},
                        {"id": "test-1", "split": "test", "params": {"x": 0.4, "y": 0.0}},
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        suites = SuiteLibrary(manager.library_assets.roots)
        suites.refresh()
        suite = suites.resolve("suite/bot-move-suite@1")

        # Resolve PolicySubject for move_v2 -> ONLY moveTo is replaced, balance stays balance_id!
        subject = resolve_policy_subject(manager, move_v2, suite, scene)
        assert subject.target.interface == "balance-bot/v1"
        assert subject.target.action == "moveTo"
        assert subject.binding["program"] == EmbodiedPolicyProgram.id
        assert subject.binding["config"]["policies"] == {"balance": balance_id, "moveTo": move_v2}

        # Apply to Catalog and verify base & applied catalog integrity
        catalog = compile_catalog(
            "rev-1",
            load_entity_types(user_lib),
            {"/World/Lab/Bot": scene_report["effectiveBindings"]["newton"][0]},
            {"/World/Lab/Bot": EmbodiedPolicyProgram.Config.model_validate(base_cfg)},
        )
        validate_base_catalog(catalog, subject)
        apply_policy_catalog(catalog, subject, scene)
        validate_policy_catalog(catalog, subject)

        # Embodiment + Controller promotion publication & runtime deployment verification
        durable_json(
            user_lib / "embodiments/mini-balancer-sim.yaml",
            {
                "id": "mini-balancer-sim",
                "title": "Mini Balancer Sim",
                "kind": "sim",
                "asset": "robot/mini-balancer@1",
                "controller": "mini-balancer-ctrl",
                "bindings": [{"scene": "bot-arena@1", "entity": "/World/Lab/Bot", "source": "newton"}],
            },
        )
        durable_json(
            user_lib / "controllers/mini-balancer-ctrl/controller.yaml",
            {
                "id": "mini-balancer-ctrl",
                "title": "Mini Balancer Controller",
                "embodiment": "mini-balancer-sim",
                "currentVersion": None,
            },
        )
        promotions = client.app.state.controller_promotions
        embodiment, controller = promotions._controller(scene, "/World/Lab/Bot")
        model = promotions._model(
            subject, embodiment, {"evaluationId": "eval-1", "at": "2026-11-12T00:00:00Z"}
        )
        assert model["actionSpace"] == "joint_position_offset"
        durable_json(user_lib / f"robot-models/{model['id']}.yaml", model)

        version_fields = {
            "embodiment": embodiment["id"],
            "robotModel": model["id"],
            "policy": move_v2,
            "program": EmbodiedPolicyProgram.id,
            "action": {"space": model["actionSpace"], "hz": 20.0},
            "observation": subject.candidateIdentity.observationSha256,
            "task": subject.candidateIdentity.task,
            "evaluationId": "eval-1",
        }
        plan = DeploymentPlan(
            promotion_id="promotion-custom-bot",
            scene=scene,
            subject=subject,
            controller=controller,
            version_fields=version_fields,
            context_hash="pending",
        )
        context = {
            "baseScene": {"id": scene.id, "contentHash": scene.content_hash},
            "subject": subject.document(),
            "controller": controller,
            "versionFields": version_fields,
            "libraryRoot": str(user_lib),
            "materialIdentities": {"/World/Lab/Bot": subject.candidateIdentity.model_dump(mode="json")},
        }
        plan = replace(plan, context_hash=fingerprint(context))
        approval = manager.approvals.submit(
            subject="policy",
            subject_id=plan.promotion_id,
            requester=Actor(kind="agent", name="test"),
            title="Promote custom balancer",
            details={"controllerPromotionId": plan.promotion_id, "contextHash": plan.context_hash},
        )
        manager.approvals.decide(approval.approvalId, "approve", Actor(kind="user", name="human"))
        promo_root = state_root / "controller-promotions"
        durable_json(
            promo_root / f"{plan.promotion_id}.json",
            {
                "schema": "lw.controller-promotion/1",
                "id": plan.promotion_id,
                "status": "approved",
                "context": context,
                "contextHash": plan.context_hash,
                "approvalId": approval.approvalId,
            },
        )
        importer = LibraryImports(manager.library_assets, manager.library, validator)
        publication = DeploymentPublication(
            manager.library_assets,
            manager.library,
            client.app.state.robot_resources,
            importer,
            promo_root,
            validate_context=lambda _: None,
            commit_context=lambda _: nullcontext(),
        )
        publication.prepare(plan)
        committed = publication.publish(plan.promotion_id)
        assert committed["phase"] == "committed"

        deployed_scene = manager.library.resolve(committed["scene"]["id"], launch=True, source="newton")
        assert scene_requires_material_authorization(deployed_scene, "newton") is True
        permit = read_committed_permit(state_root, plan.promotion_id)
        deployed_catalog = compile_catalog(
            "rev-deployed",
            load_entity_types(user_lib),
            {"/World/Lab/Bot": deployed_scene.report()["effectiveBindings"]["newton"][0]},
            {"/World/Lab/Bot": EmbodiedPolicyProgram.Config.model_validate(subject.binding["config"])},
        )
        verified = verify_runtime_deployment(deployed_scene, deployed_catalog, permit, state_root=state_root)
        assert verified.bindings["/World/Lab/Bot"]["config"]["policies"] == {
            "balance": balance_id,
            "moveTo": move_v2,
        }

        # Tampering with the sibling policy ("balance") must ALSO invalidate material verification!
        sib_onnx = user_lib / "policies/balance-bot/1/package/artifacts/policy.onnx"
        sib_onnx.write_bytes(b"corrupted-sibling")
        with pytest.raises(LibraryError):
            validate_policy_material(
                subject.candidateIdentity.model_dump(mode="json"),
                subject.binding,
                manager.library_assets.roots,
            )
