"""Unit tests for cross-scene human-approved Policy bundle authorization and tamper rejection."""

import hashlib
from copy import deepcopy

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
from packages.library.suites import SuiteLibrary
from packages.library.transactions import durable_json
from packages.policies.subjects import resolve_policy_subject
from packages.programs.biped import BipedPolicyProgram, BipedPostureProgram
from packages.programs.deployment import (
    authorize_cross_scene_policy_deployment,
    verify_runtime_deployment,
)
from packages.training.deployment_authority import read_committed_permit
from tests.gateway.test_training_jobs import TOKEN
from tests.unit.test_biped_programs import _make_biped_policies
from tests.unit.test_controller_publication import ControlledPublicationValidator
from tests.unit.test_embodied_subject_and_promotion import (
    _pin_policy_scene_hash,
    _publish_promotion,
)
from tests.unit.test_multi_policy_program import linear_onnx_bytes, write_policy_package

BIPED_ASSET_USDA = """#usda 1.0
(
    defaultPrim = "Asset"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "Asset" (prepend apiSchemas = ["PhysicsArticulationRootAPI"])
{
    def Xform "Trunk" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 1.6
        double3 xformOp:translate = (0, 0, 0.24)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.12
        }
    }
    def Xform "LeftThigh" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 0.2
        double3 xformOp:translate = (0, 0.06, 0.14)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.04
        }
    }
    def Xform "RightThigh" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 0.2
        double3 xformOp:translate = (0, -0.06, 0.14)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.04
        }
    }
    def Xform "LeftFoot" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 0.2
        double3 xformOp:translate = (0, 0.06, 0.04)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.06
        }
    }
    def Xform "RightFoot" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 0.2
        double3 xformOp:translate = (0, -0.06, 0.04)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.06
        }
    }
    def PhysicsRevoluteJoint "LeftHip" (prepend apiSchemas = ["PhysicsDriveAPI:angular"])
    {
        rel physics:body0 = </Asset/Trunk>
        rel physics:body1 = </Asset/LeftThigh>
        uniform token physics:axis = "Y"
        point3f physics:localPos0 = (0, 0.06, -0.05)
        point3f physics:localPos1 = (0, 0, 0.05)
        float physics:lowerLimit = -70.0
        float physics:upperLimit = 70.0
        float drive:angular:physics:stiffness = 25.0
        float drive:angular:physics:damping = 2.5
    }
    def PhysicsRevoluteJoint "RightHip" (prepend apiSchemas = ["PhysicsDriveAPI:angular"])
    {
        rel physics:body0 = </Asset/Trunk>
        rel physics:body1 = </Asset/RightThigh>
        uniform token physics:axis = "Y"
        point3f physics:localPos0 = (0, -0.06, -0.05)
        point3f physics:localPos1 = (0, 0, 0.05)
        float physics:lowerLimit = -70.0
        float physics:upperLimit = 70.0
        float drive:angular:physics:stiffness = 25.0
        float drive:angular:physics:damping = 2.5
    }
    def PhysicsRevoluteJoint "LeftKnee" (prepend apiSchemas = ["PhysicsDriveAPI:angular"])
    {
        rel physics:body0 = </Asset/LeftThigh>
        rel physics:body1 = </Asset/LeftFoot>
        uniform token physics:axis = "Y"
        point3f physics:localPos0 = (0, 0, -0.05)
        point3f physics:localPos1 = (0, 0, 0.05)
        float physics:lowerLimit = -70.0
        float physics:upperLimit = 70.0
        float drive:angular:physics:stiffness = 25.0
        float drive:angular:physics:damping = 2.5
    }
    def PhysicsRevoluteJoint "RightKnee" (prepend apiSchemas = ["PhysicsDriveAPI:angular"])
    {
        rel physics:body0 = </Asset/RightThigh>
        rel physics:body1 = </Asset/RightFoot>
        uniform token physics:axis = "Y"
        point3f physics:localPos0 = (0, 0, -0.05)
        point3f physics:localPos1 = (0, 0, 0.05)
        float physics:lowerLimit = -70.0
        float physics:upperLimit = 70.0
        float drive:angular:physics:stiffness = 25.0
        float drive:angular:physics:damping = 2.5
    }
}
"""


def _scene_usda(entity_prim_name: str) -> str:
    return f"""#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "World"
{{
    def PhysicsScene "PhysicsScene"
    {{
        vector3f physics:gravityDirection = (0, 0, -1)
        float physics:gravityMagnitude = 9.81
    }}
    def Xform "Lab"
    {{
        def Cube "Floor" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {{
            double size = 1.0
            double3 xformOp:translate = (0, 0, -0.05)
            float3 xformOp:scale = (6.0, 6.0, 0.1)
            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
        }}
        def Xform "{entity_prim_name}" (
            prepend references = @library:assets/robot/test-biped/1/asset.usda@
        )
        {{
        }}
    }}
}}
"""


def _write_validated_scene(
    state_root,
    user_lib,
    validator,
    *,
    name: str,
    entity_prim: str,
    devices_entities: list[dict],
    waypoints: dict,
):
    scene_dir = user_lib / "scenes" / name / "1"
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "scene.usda").write_text(_scene_usda(entity_prim), encoding="utf-8")
    scene_meta = {"name": name, "title": f"Scene {name}", "sources": ["newton"], "stage": "scene.usda"}
    (scene_dir / "scene.yaml").write_text(yaml.safe_dump(scene_meta, sort_keys=False), encoding="utf-8")
    scene_binds = {"scene": name, "entities": devices_entities}
    (scene_dir / "devices.yaml").write_text(yaml.safe_dump(scene_binds, sort_keys=False), encoding="utf-8")
    loc_doc = {"waypoints": waypoints}
    (scene_dir / "locations.yaml").write_text(yaml.safe_dump(loc_doc, sort_keys=False), encoding="utf-8")

    assets = AssetLibrary(state_root, user_root=user_lib)
    assets.startup()
    asset_dir = user_lib / "assets/robot/test-biped/1"
    report = validator.run(
        scene_dir,
        "scene",
        f"scene:{name}@1",
        scene_meta,
        assets,
        bindings=scene_binds,
        locations=loc_doc,
        expected_assets=[{"id": "robot/test-biped@1", "contentHash": content_hash(asset_dir)}],
    )
    assert report["launchable"], report
    durable_json(scene_dir / "validation.json", report)


def test_cross_scene_policy_bundle_authorizes_only_exact_committed_controller_and_rejects_tampering(
    tmp_path, monkeypatch
):
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))

    validator = ControlledPublicationValidator(state_root / "val", program_schemas)

    # 1. Write & validate generic test-biped asset
    asset_dir = user_lib / "assets/robot/test-biped/1"
    asset_dir.mkdir(parents=True)
    raw_usda = BIPED_ASSET_USDA.encode("utf-8")
    (asset_dir / "asset.usda").write_bytes(raw_usda)
    (asset_dir / "LICENSE.txt").write_text("Apache-2.0\n", encoding="utf-8")
    durable_json(
        asset_dir / "provenance.json",
        {
            "source": "generic-biped-unit-fixture",
            "files": {"asset.usda": {"sha256": hashlib.sha256(raw_usda).hexdigest(), "bytes": len(raw_usda)}},
        },
    )
    (asset_dir / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "robot/test-biped",
                "kind": "robot",
                "title": "Generic Test Biped",
                "stage": "asset.usda",
                "physics": {"massKg": 2.4, "massSource": "measured", "collision": "authored"},
                "implements": [
                    {
                        "type": "biped/v1",
                        "programs": {"newton": BipedPostureProgram.id},
                        "config": {
                            "jointKeys": [
                                "LeftHip",
                                "RightHip",
                                "LeftKnee",
                                "RightKnee",
                            ],
                            "baseBody": "Trunk",
                            "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    pre_assets = AssetLibrary(state_root, user_root=user_lib)
    pre_assets.startup()
    asset_report = validator.run(
        asset_dir,
        "asset",
        "asset:robot/test-biped@1",
        pre_assets.versions["robot/test-biped@1"].metadata,
        pre_assets,
    )
    durable_json(asset_dir / "validation.json", asset_report)

    # 2. Write Scene A (biped-arena@1) with robot at /World/Lab/ArenaBiped
    _write_validated_scene(
        state_root,
        user_lib,
        validator,
        name="biped-arena",
        entity_prim="ArenaBiped",
        devices_entities=[
            {
                "key": "/World/Lab/ArenaBiped",
                "template": "robot/test-biped@1",
                "config": {"locations": "locations.yaml"},
            }
        ],
        waypoints={"arena_wp": {"p": [1.0, 0.0, 0.0], "yaw": 0.0}},
    )

    for subdir in ("embodiments", "robot-models", "controllers/test-biped-ctrl"):
        (user_lib / subdir).mkdir(parents=True, exist_ok=True)
    (user_lib / "embodiments/test-biped.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "test-biped",
                "title": "Test Biped",
                "kind": "sim",
                "asset": "robot/test-biped@1",
                "controller": "test-biped-ctrl",
                "bindings": [
                    {"scene": "biped-arena@1", "entity": "/World/Lab/ArenaBiped", "source": "newton"}
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (user_lib / "controllers/test-biped-ctrl/controller.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "test-biped-ctrl",
                "title": "Test Biped Controller",
                "embodiment": "test-biped",
                "currentVersion": None,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    settings = Settings(state_root=state_root, recordings_root=tmp_path / "rec", node_token=TOKEN)
    app = create_app(settings, controller_publication_validator=validator)
    with TestClient(app) as client:
        manager = client.app.state.manager
        promotions = client.app.state.controller_promotions
        current_scene = manager.library.resolve("biped-arena@1", launch=True, source="newton")

        # Promote walkTo, standUp, bow sequentially on Scene A
        policies_v1 = _make_biped_policies(
            user_lib, robot="/World/Lab/ArenaBiped", scene="biped-arena@1"
        )
        promoted_policies = {}
        accumulated_comp = {}

        for idx, (action, base_pol_id) in enumerate(policies_v1.items(), start=1):
            base_name, _ = base_pol_id.removeprefix("policy/").split("@")
            base_pkg = user_lib / "policies" / base_name / "1/package"
            task_doc = yaml.safe_load((base_pkg / "task.json").read_text(encoding="utf-8")) | {
                "scene": current_scene.id
            }
            spec_doc = yaml.safe_load((base_pkg / "obs_spec.yaml").read_text(encoding="utf-8"))
            onnx_raw = (base_pkg / "artifacts/policy.onnx").read_bytes()
            pol_id = write_policy_package(
                user_lib, name=base_name, rev=2, task=task_doc, spec=spec_doc, onnx_bytes=onnx_raw
            )
            _pin_policy_scene_hash(user_lib, base_name, 2, current_scene.id, current_scene.content_hash)
            promoted_policies[action] = pol_id

            action_slug = {"walkTo": "walk-to", "standUp": "stand-up", "bow": "bow"}[action]
            suite_dir = user_lib / f"suites/biped-{action_slug}/1"
            (suite_dir / "private").mkdir(parents=True, exist_ok=True)
            params = (
                {"waypoint": "arena_wp"}
                if action == "walkTo"
                else ({"fromFall": "front"} if action == "standUp" else {})
            )
            (suite_dir / "task.yaml").write_text(
                yaml.safe_dump(
                    {
                        "name": f"biped-{action_slug}",
                        "scene": current_scene.id,
                        "robot": "/World/Lab/ArenaBiped",
                        "implements": f"biped/v1.{action}",
                        "timeoutSeconds": 5.0,
                        "success": {"entity": "/World/Lab/ArenaBiped", "property": "tiltDeg", "lte": 50.0},
                        "milestones": [
                            {
                                "id": "goal",
                                "when": {
                                    "entity": "/World/Lab/ArenaBiped",
                                    "property": "tiltDeg",
                                    "lte": 50.0,
                                },
                            }
                        ],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (suite_dir / "private/seeds.yaml").write_text(
                yaml.safe_dump({"train-1": 1, "test-1": 2}), encoding="utf-8"
            )
            (suite_dir / "suite.yaml").write_text(
                yaml.safe_dump(
                    {
                        "id": f"suite/biped-{action_slug}",
                        "title": f"Suite {action}",
                        "layer": "embodied",
                        "source": "newton",
                        "scene": current_scene.id,
                        "sceneContentHash": current_scene.content_hash,
                        "episodes": [
                            {"id": "train-1", "split": "train", "params": params},
                            {"id": "test-1", "split": "test", "params": params},
                        ],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            suites = SuiteLibrary(manager.library_assets.roots)
            suites.refresh()
            suite = suites.resolve(f"suite/biped-{action_slug}@1")
            subject = resolve_policy_subject(manager, pol_id, suite, current_scene)
            emb, ctrl = promotions._controller(current_scene, "/World/Lab/ArenaBiped")
            model = promotions._model(
                subject, emb, {"evaluationId": f"eval-{idx}", "at": "2026-11-12T00:00:00Z"}
            )
            durable_json(user_lib / f"robot-models/{model['id']}.yaml", model)
            materials = promotions._materials(current_scene, subject)
            accumulated_comp[action] = promotions._policy_composition(pol_id)
            version_fields = {
                "embodiment": emb["id"],
                "robotModel": model["id"],
                "policy": pol_id,
                "program": BipedPolicyProgram.id,
                "action": accumulated_comp[action]["action"],
                "observation": subject.candidateIdentity.observationSha256,
                "task": subject.candidateIdentity.task,
                "policies": deepcopy(accumulated_comp),
                "evaluationId": f"eval-{idx}",
            }
            committed = _publish_promotion(
                manager=manager,
                robot_resources=client.app.state.robot_resources,
                validator=validator,
                state_root=state_root,
                user_lib=user_lib,
                promotion_id=f"promotion-biped-{idx}",
                scene=current_scene,
                subject=subject,
                controller=ctrl,
                version_fields=version_fields,
                materials=materials,
            )
            current_scene = manager.library.resolve(committed["scene"]["id"], launch=True, source="newton")

        assert current_scene.id == "biped-arena@4"
        committed_permit = read_committed_permit(state_root, "promotion-biped-3")

        # 3. Write Scene B (biped-patrol@1) with robot at /World/Lab/PatrolBiped and DIFFERENT Waypoints!
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-patrol",
            entity_prim="PatrolBiped",
            devices_entities=[
                {
                    "key": "/World/Lab/PatrolBiped",
                    "type": "biped/v1",
                    "program": BipedPolicyProgram.id,
                    "config": {
                        "jointKeys": [
                            "/World/Lab/PatrolBiped/LeftHip",
                            "/World/Lab/PatrolBiped/RightHip",
                            "/World/Lab/PatrolBiped/LeftKnee",
                            "/World/Lab/PatrolBiped/RightKnee",
                        ],
                        "baseBody": "/World/Lab/PatrolBiped/Trunk",
                        "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                        "locations": "locations.yaml",
                        "policies": promoted_policies,
                    },
                }
            ],
            waypoints={
                "patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57},
                "patrol_west": {"p": [-1.0, 0.0, 0.0], "yaw": 3.14},
            },
        )
        manager.library.refresh()
        patrol_scene = manager.library.resolve("biped-patrol@1", launch=True, source="newton")

        cross_permit = authorize_cross_scene_policy_deployment(patrol_scene, state_root=state_root)
        assert cross_permit == committed_permit

        patrol_binding = patrol_scene.report()["effectiveBindings"]["newton"][0]
        patrol_catalog = compile_catalog(
            "rev-patrol",
            load_entity_types(),
            {"/World/Lab/PatrolBiped": patrol_binding},
            {"/World/Lab/PatrolBiped": BipedPolicyProgram.Config.model_validate(patrol_binding["config"])},
        )
        verified = verify_runtime_deployment(
            patrol_scene, patrol_catalog, cross_permit, state_root=state_root
        )
        assert verified.bindings["/World/Lab/PatrolBiped"]["config"]["policies"] == promoted_policies

        # Tampering with a non-location config parameter on the Runner catalog MUST fail!
        tampered_catalog = deepcopy(patrol_catalog)
        tampered_catalog.entities["/World/Lab/PatrolBiped"]["config"]["maxLinearSpeed"] = 9.9
        with pytest.raises(LibraryError) as exc_param:
            verify_runtime_deployment(patrol_scene, tampered_catalog, cross_permit, state_root=state_root)
        assert exc_param.value.detail["code"] == "simulation_deployment_changed"

        # Importing Scene C with an UNPROMOTED policy MUST fail with deployment_authorization_required!
        rogue_id = write_policy_package(
            user_lib,
            name="rogue-walk",
            rev=1,
            task={
                "name": "rogue-walk",
                "title": "Rogue",
                "scene": "biped-arena@1",
                "robot": "/World/Lab/ArenaBiped",
                "implements": "biped/v1.walkTo",
                "episode": {
                    "params": {"waypoint": "arena_wp"},
                    "commandEnvelope": [[-0.2, 0.25], [-0.12, 0.12], [-0.6, 0.6]],
                },
                "success": {"property": "phase", "equals": "idle"},
                "control": {"hz": 20.0, "action": "joint_position_offset"},
            },
            spec=yaml.safe_load((user_lib / "policies/biped-walk/2/package/obs_spec.yaml").read_bytes()),
            onnx_bytes=linear_onnx_bytes(np.zeros((13, 4), dtype=np.float32)),
        )
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-rogue",
            entity_prim="PatrolBiped",
            devices_entities=[
                {
                    "key": "/World/Lab/PatrolBiped",
                    "type": "biped/v1",
                    "program": BipedPolicyProgram.id,
                    "config": {
                        "jointKeys": [
                            "/World/Lab/PatrolBiped/LeftHip",
                            "/World/Lab/PatrolBiped/RightHip",
                            "/World/Lab/PatrolBiped/LeftKnee",
                            "/World/Lab/PatrolBiped/RightKnee",
                        ],
                        "baseBody": "/World/Lab/PatrolBiped/Trunk",
                        "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                        "locations": "locations.yaml",
                        "policies": promoted_policies | {"walkTo": rogue_id},
                    },
                }
            ],
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
        )
        manager.library.refresh()
        rogue_scene = manager.library.resolve("biped-rogue@1", launch=True, source="newton")

        with pytest.raises(PlanError) as exc_rogue:
            authorize_cross_scene_policy_deployment(rogue_scene, state_root=state_root)
        assert exc_rogue.value.detail["code"] == "deployment_authorization_required"

        rogue_http = client.post(
            "/api/v1/sessions",
            json={
                "sceneVersion": "biped-rogue@1",
                "source": "newton",
                "continueWithoutObservers": True,
            },
        )
        assert rogue_http.status_code == 409, rogue_http.text
        assert rogue_http.json()["detail"]["code"] == "deployment_authorization_required"
