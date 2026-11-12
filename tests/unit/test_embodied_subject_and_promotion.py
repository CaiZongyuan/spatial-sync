"""De-armified policy subject resolution, multi-policy override and Controller promotion authority."""

import hashlib
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
from packages.info.plans import PlanError
from packages.library.assets import AssetLibrary
from packages.library.common import LibraryError, content_hash, load_entity_types
from packages.library.imports import LibraryImports
from packages.library.suites import SuiteLibrary
from packages.library.transactions import durable_json
from packages.policies.subjects import (
    apply_policy_catalog,
    resolve_policy_subject,
    validate_base_catalog,
    validate_policy_catalog,
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
from tests.fixtures.policy import policy_task
from tests.fixtures.training_task import task_document
from tests.gateway.test_training_jobs import TOKEN
from tests.unit.test_controller_publication import ControlledPublicationValidator
from tests.unit.test_multi_policy_program import BALANCE_BOT_TYPE, linear_onnx_bytes, write_policy_package


def _write_custom_scene_and_resources(user_lib, extra_types, *, binding_program, binding_config):
    extra_types.mkdir(parents=True, exist_ok=True)
    (extra_types / "balance-bot.v1.yaml").write_text(
        yaml.safe_dump(BALANCE_BOT_TYPE, sort_keys=False), encoding="utf-8"
    )

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


def _validate_asset_and_scene(state_root, user_lib, asset_dir, scene_dir, validator):
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
    return scene_report


def _pin_policy_scene_hash(user_lib, name: str, rev: int, scene_id: str, scene_hash: str):
    p_dir = user_lib / "policies" / name / str(rev)
    pkg = p_dir / "package"
    prov = yaml.safe_load((pkg / "policy.json").read_text(encoding="utf-8"))
    prov["scene"] = scene_id
    prov["sceneContentHash"] = scene_hash
    durable_json(pkg / "policy.json", prov)
    meta = yaml.safe_load((p_dir / "metadata.json").read_text(encoding="utf-8"))
    meta["contentHash"] = content_hash(pkg)
    durable_json(p_dir / "metadata.json", meta)


def _write_suite(user_lib, roots, *, name: str, rev: int, scene, implements: str, prop: str, params: dict):
    suite_dir = user_lib / "suites" / name / str(rev)
    (suite_dir / "private").mkdir(parents=True, exist_ok=True)
    goal = {"entity": "/World/Lab/Bot", "property": prop, "lte": 0.05}
    (suite_dir / "task.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "scene": scene.id,
                "robot": "/World/Lab/Bot",
                "implements": implements,
                "timeoutSeconds": 5.0,
                "success": goal,
                "milestones": [{"id": "goal", "when": goal}],
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
                "id": f"suite/{name}",
                "title": f"Suite {name}",
                "layer": "embodied",
                "source": "newton",
                "scene": scene.id,
                "sceneContentHash": scene.content_hash,
                "episodes": [
                    {"id": "train-1", "split": "train", "params": params},
                    {"id": "test-1", "split": "test", "params": params},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    suites = SuiteLibrary(roots)
    suites.refresh()
    return suites.resolve(f"suite/{name}@{rev}")


def _publish_promotion(
    *,
    manager,
    robot_resources,
    validator,
    state_root,
    user_lib,
    promotion_id: str,
    scene,
    subject,
    controller: dict,
    version_fields: dict,
    materials: dict,
):
    plan = DeploymentPlan(
        promotion_id=promotion_id,
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
        "materialIdentities": materials,
    }
    plan = replace(plan, context_hash=fingerprint(context))
    approval = manager.approvals.submit(
        subject="policy",
        subject_id=plan.promotion_id,
        requester=Actor(kind="agent", name="test"),
        title=f"Promote {promotion_id}",
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
        robot_resources,
        importer,
        promo_root,
        validate_context=lambda _: None,
        commit_context=lambda _: nullcontext(),
    )
    publication.prepare(plan)
    committed = publication.publish(plan.promotion_id)
    assert committed["phase"] == "committed"
    return committed


def test_extra_device_types_dir_boundary_and_user_library_isolation(tmp_path, monkeypatch):
    user_lib = tmp_path / "library"
    (user_lib / "types").mkdir(parents=True)
    (user_lib / "types/balance-bot.v1.yaml").write_text(
        yaml.safe_dump(BALANCE_BOT_TYPE), encoding="utf-8"
    )
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.delenv("SPATIAL_EXTRA_DEVICE_TYPES_DIR", raising=False)

    # Ambient <user_library>/types/*.yaml is NEVER scanned into global device types
    assert "balance-bot/v1" not in load_entity_types()

    extra_dir = tmp_path / "extra-types"
    extra_dir.mkdir(parents=True)
    (extra_dir / "balance-bot.v1.yaml").write_text(yaml.safe_dump(BALANCE_BOT_TYPE), encoding="utf-8")
    monkeypatch.setenv("SPATIAL_EXTRA_DEVICE_TYPES_DIR", str(extra_dir))
    types = load_entity_types()
    assert "balance-bot/v1" in types and "robot-arm/v1" in types

    # Shadowing a builtin type is strictly forbidden
    dup = deepcopy(BALANCE_BOT_TYPE) | {"type": "robot-arm/v1"}
    collide = extra_dir / "collide.yaml"
    collide.write_text(yaml.safe_dump(dup), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate entity type"):
        load_entity_types()
    collide.unlink()

    # Symlinked YAML files in SPATIAL_EXTRA_DEVICE_TYPES_DIR are rejected
    outside = tmp_path / "outside.yaml"
    outside.write_text(
        yaml.safe_dump(deepcopy(BALANCE_BOT_TYPE) | {"type": "other-bot/v1"}), encoding="utf-8"
    )
    link = extra_dir / "linked.yaml"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="regular local file"):
        load_entity_types()
    link.unlink()


def test_task_library_validates_command_envelope_against_obs_spec(tmp_path, monkeypatch):
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    extra_types = tmp_path / "extra-types"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SPATIAL_EXTRA_DEVICE_TYPES_DIR", str(extra_types))

    asset_dir, scene_dir, _ = _write_custom_scene_and_resources(
        user_lib,
        extra_types,
        binding_program=EmbodiedPostureProgram.id,
        binding_config={
            "jointKeys": ["/World/Lab/Bot/j1", "/World/Lab/Bot/j2"],
            "baseBody": "/World/Lab/Bot/Base",
        },
    )
    validator = ControlledPublicationValidator(state_root / "val", program_schemas)
    _validate_asset_and_scene(state_root, user_lib, asset_dir, scene_dir, validator)

    cmd_spec = {
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
                "lower": [-1.0, -1.0, 0.05],
                "upper": [1.0, 1.0, 0.25],
            }
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": ["/World/Lab/Bot/j1", "/World/Lab/Bot/j2"],
            "units": ["rad", "rad"],
            "defaultPosition": [0.0, 0.0],
            "scale": [0.2, 0.2],
            "lower": [-0.8, -0.8],
            "upper": [0.8, 0.8],
        },
    }
    base_task = task_document("bot-move-cmd") | {
        "title": "Bot Move Command",
        "scene": "bot-arena@1",
        "robot": "/World/Lab/Bot",
        "implements": "balance-bot/v1.moveTo",
        "success": {"entity": "/World/Lab/Bot", "property": "distanceToGoal", "lte": 0.05},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
        "evaluation": {
            "scene": "bot-arena@1",
            "episodes": 1,
            "seeds": [1],
            "params": {"x": [0.3], "y": [0.0], "speed": [0.15]},
        },
    }

    settings = Settings(state_root=state_root, recordings_root=tmp_path / "rec", node_token=TOKEN)
    with TestClient(create_app(settings, controller_publication_validator=validator)) as client:
        # 1. Missing commandEnvelope when obs_spec declares source="command" -> 422
        missing_env = deepcopy(base_task) | {
            "episode": {
                "params": {"speed": 0.15},
                "paramBounds": {"x": [-0.5, 0.5], "y": [-0.5, 0.5], "speed": [0.05, 0.25]},
            }
        }
        r_missing = client.post(
            "/api/v1/tasks",
            content=policy_task(tmp_path / "pkg-missing", spec=cmd_spec, task=missing_env),
            headers={"Content-Type": "application/x-tar"},
        )
        assert r_missing.status_code == 422, r_missing.text

        # 2. commandEnvelope wider than obs_spec [lower, upper] -> 422
        wider_env = deepcopy(base_task) | {
            "episode": {
                "params": {"speed": 0.15},
                "paramBounds": {"x": [-0.5, 0.5], "y": [-0.5, 0.5], "speed": [0.05, 0.25]},
                "commandEnvelope": [[-1.5, 1.5], [-1.0, 1.0], [0.05, 0.25]],
            }
        }
        r_wider = client.post(
            "/api/v1/tasks",
            content=policy_task(tmp_path / "pkg-wider", spec=cmd_spec, task=wider_env),
            headers={"Content-Type": "application/x-tar"},
        )
        assert r_wider.status_code == 422, r_wider.text

        # 3. Matching width and commandEnvelope within obs_spec [lower, upper] -> 201
        valid_env = deepcopy(base_task) | {
            "episode": {
                "params": {"speed": 0.15},
                "paramBounds": {"x": [-0.5, 0.5], "y": [-0.5, 0.5], "speed": [0.05, 0.25]},
                "commandEnvelope": [[-0.8, 0.8], [-0.8, 0.8], [0.05, 0.25]],
            }
        }
        r_valid = client.post(
            "/api/v1/tasks",
            content=policy_task(tmp_path / "pkg-valid", spec=cmd_spec, task=valid_env),
            headers={"Content-Type": "application/x-tar"},
        )
        assert r_valid.status_code == 201, r_valid.text


def test_custom_multi_policy_subject_usd_joint_limits_and_multi_action_promotion_tamper_guard(
    tmp_path, monkeypatch
):
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    extra_types = tmp_path / "extra-types"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SPATIAL_EXTRA_DEVICE_TYPES_DIR", str(extra_types))

    joints = ["/World/Lab/Bot/j1", "/World/Lab/Bot/j2"]
    base = "/World/Lab/Bot/Base"

    balance_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "gravity",
                "source": "projected_gravity",
                "key": base,
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
    write_policy_package(
        user_lib,
        name="balance-bot",
        rev=1,
        task=balance_task,
        spec=balance_spec,
        onnx_bytes=linear_onnx_bytes(np.zeros((3, 2), dtype=np.float32)),
    )
    balance_v2 = write_policy_package(
        user_lib,
        name="balance-bot",
        rev=2,
        task=balance_task,
        spec=balance_spec,
        onnx_bytes=linear_onnx_bytes(np.ones((3, 2), dtype=np.float32) * 0.05),
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
                "lower": [-1.0, -1.0, 0.05],
                "upper": [1.0, 1.0, 0.25],
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
    move_task_v1 = {
        "name": "move-bot",
        "title": "Move",
        "scene": "bot-arena@1",
        "robot": "/World/Lab/Bot",
        "implements": "balance-bot/v1.moveTo",
        "episode": {
            "params": {"speed": 0.15},
            "paramBounds": {"x": [-1.0, 1.0], "y": [-1.0, 1.0], "speed": [0.05, 0.25]},
            "commandEnvelope": [[-1.0, 1.0], [-1.0, 1.0], [0.05, 0.25]],
        },
        "success": {"property": "distanceToGoal", "lte": 0.05},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    write_policy_package(
        user_lib,
        name="move-bot",
        rev=1,
        task=move_task_v1,
        spec=move_spec,
        onnx_bytes=linear_onnx_bytes(np.zeros((3, 2), dtype=np.float32)),
    )

    # Action bounds [-2.0, 2.0] rad exceed USD physical limits [-90, 90] deg ([-pi/2, pi/2] rad)
    move_oob_spec = deepcopy(move_spec)
    move_oob_spec["action"]["lower"] = [-2.0, -2.0]
    move_oob_spec["action"]["upper"] = [2.0, 2.0]
    move_oob = write_policy_package(
        user_lib,
        name="move-bot-oob",
        rev=1,
        task=deepcopy(move_task_v1) | {"name": "move-bot-oob"},
        spec=move_oob_spec,
        onnx_bytes=linear_onnx_bytes(np.zeros((3, 2), dtype=np.float32)),
    )

    base_cfg = {
        "jointKeys": joints,
        "baseBody": base,
    }
    asset_dir, scene_dir, _ = _write_custom_scene_and_resources(
        user_lib, extra_types, binding_program=EmbodiedPostureProgram.id, binding_config=base_cfg
    )
    validator = ControlledPublicationValidator(state_root / "val", program_schemas)
    scene_report = _validate_asset_and_scene(state_root, user_lib, asset_dir, scene_dir, validator)

    settings = Settings(state_root=state_root, recordings_root=tmp_path / "rec", node_token=TOKEN)
    app = create_app(settings, controller_publication_validator=validator)
    with TestClient(app) as client:
        manager = client.app.state.manager
        scene_v1 = manager.library.resolve("bot-arena@1", launch=True, source="newton")
        assert scene_requires_material_authorization(scene_v1, "newton") is False

        for name, rev in (("balance-bot", 1), ("balance-bot", 2), ("move-bot", 1), ("move-bot-oob", 1)):
            _pin_policy_scene_hash(user_lib, name, rev, scene_v1.id, scene_v1.content_hash)

        suite_bal_v1 = _write_suite(
            user_lib,
            manager.library_assets.roots,
            name="bot-balance-suite",
            rev=1,
            scene=scene_v1,
            implements="balance-bot/v1.balance",
            prop="tiltDeg",
            params={},
        )
        suite_move_v1 = _write_suite(
            user_lib,
            manager.library_assets.roots,
            name="bot-move-suite",
            rev=1,
            scene=scene_v1,
            implements="balance-bot/v1.moveTo",
            prop="distanceToGoal",
            params={"x": 0.4, "y": 0.0, "speed": 0.15},
        )

        # Physical USD joint limits reject out-of-range spec.action bounds
        with pytest.raises(LibraryError, match="physical joint limits") as exc_oob:
            resolve_policy_subject(manager, move_oob, suite_move_v1, scene_v1)
        assert exc_oob.value.detail["code"] == "policy_action_binding_mismatch"

        # Action 1: Resolve & promote balance_v2 on bot-arena@1 -> bot-arena@2, ControllerVersion v1
        subject_bal = resolve_policy_subject(manager, balance_v2, suite_bal_v1, scene_v1)
        assert subject_bal.target.interface == "balance-bot/v1"
        assert subject_bal.target.action == "balance"
        assert subject_bal.binding["program"] == EmbodiedPolicyProgram.id
        assert subject_bal.binding["config"]["policies"] == {"balance": balance_v2}

        catalog_v1 = compile_catalog(
            "rev-1",
            load_entity_types(),
            {"/World/Lab/Bot": scene_report["effectiveBindings"]["newton"][0]},
            {"/World/Lab/Bot": EmbodiedPostureProgram.Config.model_validate(base_cfg)},
        )
        validate_base_catalog(catalog_v1, subject_bal)
        apply_policy_catalog(catalog_v1, subject_bal, scene_v1)
        validate_policy_catalog(catalog_v1, subject_bal)

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
        emb_1, ctrl_1 = promotions._controller(scene_v1, "/World/Lab/Bot")
        model_1 = promotions._model(
            subject_bal, emb_1, {"evaluationId": "eval-bal-1", "at": "2026-11-12T00:00:00Z"}
        )
        assert model_1["actionSpace"] == "joint_position_offset"
        durable_json(user_lib / f"robot-models/{model_1['id']}.yaml", model_1)

        materials_1 = promotions._materials(scene_v1, subject_bal)
        assert materials_1["/World/Lab/Bot"]["kind"] == "policy-bundle"
        assert set(materials_1["/World/Lab/Bot"]["policies"]) == {"balance"}

        policies_comp_1 = {"balance": promotions._policy_composition(balance_v2)}
        version_fields_1 = {
            "embodiment": emb_1["id"],
            "robotModel": model_1["id"],
            "policy": balance_v2,
            "program": EmbodiedPolicyProgram.id,
            "action": policies_comp_1["balance"]["action"],
            "observation": subject_bal.candidateIdentity.observationSha256,
            "task": subject_bal.candidateIdentity.task,
            "policies": policies_comp_1,
            "evaluationId": "eval-bal-1",
        }
        committed_1 = _publish_promotion(
            manager=manager,
            robot_resources=client.app.state.robot_resources,
            validator=validator,
            state_root=state_root,
            user_lib=user_lib,
            promotion_id="promotion-bot-balance",
            scene=scene_v1,
            subject=subject_bal,
            controller=ctrl_1,
            version_fields=version_fields_1,
            materials=materials_1,
        )
        scene_v2 = manager.library.resolve(committed_1["scene"]["id"], launch=True, source="newton")
        assert scene_v2.id == "bot-arena@2"
        permit_1 = read_committed_permit(state_root, "promotion-bot-balance")
        assert permit_1["controller"]["version"] == 1
        assert set(permit_1["controller"]["document"]["policies"]) == {"balance"}

        # Action 2: Promote move_v2 on bot-arena@2 -> bot-arena@3, ControllerVersion v2
        move_task_v2 = deepcopy(move_task_v1) | {"scene": scene_v2.id}
        move_v2 = write_policy_package(
            user_lib,
            name="move-bot",
            rev=2,
            task=move_task_v2,
            spec=move_spec,
            onnx_bytes=linear_onnx_bytes(np.ones((3, 2), dtype=np.float32) * 0.1),
        )
        _pin_policy_scene_hash(user_lib, "move-bot", 2, scene_v2.id, scene_v2.content_hash)
        suite_move_v2 = _write_suite(
            user_lib,
            manager.library_assets.roots,
            name="bot-move-suite",
            rev=2,
            scene=scene_v2,
            implements="balance-bot/v1.moveTo",
            prop="distanceToGoal",
            params={"x": 0.4, "y": 0.0, "speed": 0.15},
        )
        subject_move = resolve_policy_subject(manager, move_v2, suite_move_v2, scene_v2)
        assert subject_move.binding["config"]["policies"] == {"balance": balance_v2, "moveTo": move_v2}

        emb_2, ctrl_2 = promotions._controller(scene_v2, "/World/Lab/Bot")
        assert ctrl_2["currentVersion"] == 1
        model_2 = promotions._model(
            subject_move, emb_2, {"evaluationId": "eval-move-2", "at": "2026-11-12T00:05:00Z"}
        )
        durable_json(user_lib / f"robot-models/{model_2['id']}.yaml", model_2)

        materials_2 = promotions._materials(scene_v2, subject_move)
        assert materials_2["/World/Lab/Bot"]["kind"] == "policy-bundle"
        assert materials_2["/World/Lab/Bot"]["policies"]["balance"]["id"] == balance_v2
        assert materials_2["/World/Lab/Bot"]["policies"]["moveTo"]["id"] == move_v2

        policies_comp_2 = {
            "balance": deepcopy(ctrl_2["current"]["policies"]["balance"]),
            "moveTo": promotions._policy_composition(move_v2),
        }
        version_fields_2 = {
            "embodiment": emb_2["id"],
            "robotModel": model_2["id"],
            "policy": move_v2,
            "program": EmbodiedPolicyProgram.id,
            "action": policies_comp_2["moveTo"]["action"],
            "observation": subject_move.candidateIdentity.observationSha256,
            "task": subject_move.candidateIdentity.task,
            "policies": policies_comp_2,
            "evaluationId": "eval-move-2",
        }
        committed_2 = _publish_promotion(
            manager=manager,
            robot_resources=client.app.state.robot_resources,
            validator=validator,
            state_root=state_root,
            user_lib=user_lib,
            promotion_id="promotion-bot-move",
            scene=scene_v2,
            subject=subject_move,
            controller=ctrl_2,
            version_fields=version_fields_2,
            materials=materials_2,
        )
        scene_v3 = manager.library.resolve(committed_2["scene"]["id"], launch=True, source="newton")
        assert scene_v3.id == "bot-arena@3"
        assert scene_requires_material_authorization(scene_v3, "newton") is True

        permit_2 = read_committed_permit(state_root, "promotion-bot-move")
        assert permit_2["controller"]["version"] == 2
        ctrl_v2_doc = permit_2["controller"]["document"]
        assert ctrl_v2_doc["policies"]["balance"]["policy"] == balance_v2
        assert ctrl_v2_doc["policies"]["moveTo"]["policy"] == move_v2

        deployed_catalog = compile_catalog(
            "rev-deployed",
            load_entity_types(),
            {"/World/Lab/Bot": scene_v3.report()["effectiveBindings"]["newton"][0]},
            {"/World/Lab/Bot": EmbodiedPolicyProgram.Config.model_validate(subject_move.binding["config"])},
        )
        verified = verify_runtime_deployment(scene_v3, deployed_catalog, permit_2, state_root=state_root)
        assert verified.bindings["/World/Lab/Bot"]["config"]["policies"] == {
            "balance": balance_v2,
            "moveTo": move_v2,
        }

        # Regression: Tampering with previously promoted sibling policy ("balance-bot@2") AFTER promoting
        # "moveTo" MUST be caught and rejected by read_committed_permit!
        sib_onnx = user_lib / "policies/balance-bot/2/package/artifacts/policy.onnx"
        orig_onnx = sib_onnx.read_bytes()
        sib_onnx.write_bytes(b"corrupted-sibling")
        with pytest.raises(PlanError) as exc_raw:
            read_committed_permit(state_root, "promotion-bot-move")
        assert exc_raw.value.detail["code"] == "deployment_authorization_required"

        # Restore original bytes -> permit verifies cleanly again
        sib_onnx.write_bytes(orig_onnx)
        assert read_committed_permit(state_root, "promotion-bot-move")["controller"]["version"] == 2

        # Even if an attacker rewrites metadata.json + policy.json with internally consistent hashes
        # (so PolicyLibrary.resolve passes), read_committed_permit STILL rejects the sibling tamper
        # against the committed CandidateIdentity pinned in materialIdentities!
        write_policy_package(
            user_lib,
            name="balance-bot",
            rev=2,
            task=balance_task,
            spec=balance_spec,
            onnx_bytes=linear_onnx_bytes(np.ones((3, 2), dtype=np.float32) * 0.99),
        )
        _pin_policy_scene_hash(user_lib, "balance-bot", 2, scene_v1.id, scene_v1.content_hash)
        manager.policies.resolve(balance_v2)
        with pytest.raises(PlanError) as exc_rehashed:
            read_committed_permit(state_root, "promotion-bot-move")
        assert exc_rehashed.value.detail["code"] == "deployment_authorization_required"
