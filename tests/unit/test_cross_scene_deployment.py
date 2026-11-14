"""Unit tests for cross-scene human-approved Policy bundle authorization and tamper rejection."""

import hashlib
from copy import deepcopy
from pathlib import Path

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
    rev: int = 1,
    entity_prim: str = "PatrolBiped",
    devices_entities: list[dict],
    waypoints: dict,
    scene_usda_text: str | None = None,
    asset_id: str = "robot/test-biped@1",
    asset_dir=None,
    expected_assets: list[dict] | None = None,
):
    scene_dir = user_lib / "scenes" / name / str(rev)
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "scene.usda").write_text(
        scene_usda_text if scene_usda_text is not None else _scene_usda(entity_prim),
        encoding="utf-8",
    )
    scene_meta = {"name": name, "title": f"Scene {name}", "sources": ["newton"], "stage": "scene.usda"}
    (scene_dir / "scene.yaml").write_text(yaml.safe_dump(scene_meta, sort_keys=False), encoding="utf-8")
    scene_binds = {"scene": name, "entities": devices_entities}
    (scene_dir / "devices.yaml").write_text(yaml.safe_dump(scene_binds, sort_keys=False), encoding="utf-8")
    loc_doc = {"waypoints": waypoints}
    (scene_dir / "locations.yaml").write_text(yaml.safe_dump(loc_doc, sort_keys=False), encoding="utf-8")

    from packages.library import common as library_common

    resolved_builtin = Path(library_common.BUILTIN_ROOT).resolve()
    resolved_target = Path(user_lib).resolve()
    assets = AssetLibrary(
        state_root,
        builtin_root=resolved_builtin,
        user_root=(
            (resolved_target / "__no_user__") if resolved_target == resolved_builtin else resolved_target
        ),
    )
    assets.startup()
    resolved_asset_dir = asset_dir if asset_dir is not None else (user_lib / "assets/robot/test-biped/1")
    exp = (
        expected_assets
        if expected_assets is not None
        else [{"id": asset_id, "contentHash": content_hash(resolved_asset_dir)}]
    )
    report = validator.run(
        scene_dir,
        "scene",
        f"scene:{name}@{rev}",
        scene_meta,
        assets,
        bindings=scene_binds,
        locations=loc_doc,
        expected_assets=exp,
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
        permit_v2 = read_committed_permit(state_root, "promotion-biped-2")
        committed_permit = read_committed_permit(state_root, "promotion-biped-3")

        # Superseded Controller v2 policies MUST be rejected cross-scene once v3 is currentVersion!
        v2_policies = deepcopy(permit_v2["binding"]["config"]["policies"])
        assert v2_policies != promoted_policies
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-patrol-stale-v2",
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
                        "policies": v2_policies,
                    },
                }
            ],
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
        )
        manager.library.refresh()
        stale_v2_scene = manager.library.resolve("biped-patrol-stale-v2@1", launch=True, source="newton")
        with pytest.raises(PlanError) as exc_stale_v2:
            authorize_cross_scene_policy_deployment(stale_v2_scene, state_root=state_root)
        assert exc_stale_v2.value.detail["code"] == "deployment_authorization_required"

        stale_v2_binding = stale_v2_scene.report()["effectiveBindings"]["newton"][0]
        stale_v2_catalog = compile_catalog(
            "rev-stale-v2",
            load_entity_types(),
            {"/World/Lab/PatrolBiped": stale_v2_binding},
            {
                "/World/Lab/PatrolBiped": BipedPolicyProgram.Config.model_validate(
                    stale_v2_binding["config"]
                )
            },
        )
        with pytest.raises(LibraryError) as exc_stale_v2_rt:
            verify_runtime_deployment(
                stale_v2_scene, stale_v2_catalog, permit_v2, state_root=state_root
            )
        assert exc_stale_v2_rt.value.detail["code"] == "simulation_deployment_changed"

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

        # 4. Cross-scene USD Subtree Integrity: inline USD prims, non-rigid scale, and physical `over`
        # overrides (mass, joint limits, drive stiffness/maxForce) MUST all be rejected!
        patrol_entity_bind = [
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
        ]
        inline_robot_body = BIPED_ASSET_USDA.split('def Xform "Asset"', 1)[1]
        inline_scene_usda = f"""#usda 1.0
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
        def Xform "PatrolBiped"{inline_robot_body.replace("/Asset/", "/World/Lab/PatrolBiped/")}
    }}
}}
"""
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-inline",
            devices_entities=patrol_entity_bind,
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
            scene_usda_text=inline_scene_usda,
            expected_assets=[],
        )

        def _over_scene_usda(robot_block: str) -> str:
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
        def Xform "PatrolBiped" (
            prepend references = @library:assets/robot/test-biped/1/asset.usda@
        )
        {{
{robot_block}
        }}
    }}
}}
"""

        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-scaled",
            devices_entities=patrol_entity_bind,
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
            scene_usda_text=_over_scene_usda(
                '            float3 xformOp:scale = (1.5, 1.0, 1.0)\n'
                '            uniform token[] xformOpOrder = ["xformOp:scale"]'
            ),
        )
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-over-mass",
            devices_entities=patrol_entity_bind,
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
            scene_usda_text=_over_scene_usda(
                '            over "Trunk"\n'
                "            {\n"
                "                float physics:mass = 50.0\n"
                "            }"
            ),
        )
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-over-drive",
            devices_entities=patrol_entity_bind,
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
            scene_usda_text=_over_scene_usda(
                '            over "LeftHip"\n'
                "            {\n"
                "                float drive:angular:physics:stiffness = 2500.0\n"
                "                float drive:angular:physics:maxForce = 999.0\n"
                "                float physics:lowerLimit = -120.0\n"
                "            }"
            ),
        )
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-over-mjc",
            devices_entities=patrol_entity_bind,
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
            scene_usda_text=_over_scene_usda(
                '            over "LeftHip"\n'
                "            {\n"
                "                custom float mjc:armature = 0.95\n"
                "            }"
            ),
        )
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-over-mjc-actuator",
            devices_entities=patrol_entity_bind,
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
            scene_usda_text=_over_scene_usda(
                '            def MjcActuator "BypassActuator"\n'
                "            {\n"
                "                custom rel mjc:target = </World/Lab/PatrolBiped/LeftHip>\n"
                "                custom float mjc:gear = 5.0\n"
                "            }"
            ),
        )
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-rigid-placed",
            devices_entities=patrol_entity_bind,
            waypoints={"patrol_north": {"p": [0.5, 1.2, 0.0], "yaw": 1.57}},
            scene_usda_text=_over_scene_usda(
                '            double3 xformOp:translate = (0.5, -0.2, 0.0)\n'
                '            quatf xformOp:orient = (0.70710678, 0, 0, 0.70710678)\n'
                '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:orient"]'
            ),
        )

        manager.library.refresh()
        for tampered_name in (
            "biped-inline@1",
            "biped-scaled@1",
            "biped-over-mass@1",
            "biped-over-drive@1",
            "biped-over-mjc@1",
            "biped-over-mjc-actuator@1",
        ):
            tampered_scene = manager.library.resolve(tampered_name, launch=True, source="newton")
            with pytest.raises(PlanError) as exc_tampered:
                authorize_cross_scene_policy_deployment(tampered_scene, state_root=state_root)
            assert exc_tampered.value.detail["code"] == "deployment_authorization_required", tampered_name

        placed_scene = manager.library.resolve("biped-rigid-placed@1", launch=True, source="newton")
        assert (
            authorize_cross_scene_policy_deployment(placed_scene, state_root=state_root) == committed_permit
        )

        # 5. Post-promotion Embodiment YAML tampering (swapping asset to another robot) MUST fail closed!
        other_asset_dir = user_lib / "assets/robot/other-biped/1"
        other_asset_dir.mkdir(parents=True)
        other_usda = BIPED_ASSET_USDA.replace("float physics:mass = 1.6", "float physics:mass = 9.6").encode(
            "utf-8"
        )
        (other_asset_dir / "asset.usda").write_bytes(other_usda)
        (other_asset_dir / "LICENSE.txt").write_text("Apache-2.0\n", encoding="utf-8")
        durable_json(
            other_asset_dir / "provenance.json",
            {
                "source": "other-biped-fixture",
                "files": {
                    "asset.usda": {
                        "sha256": hashlib.sha256(other_usda).hexdigest(),
                        "bytes": len(other_usda),
                    }
                },
            },
        )
        (other_asset_dir / "asset.yaml").write_text(
            (asset_dir / "asset.yaml").read_text(encoding="utf-8").replace("test-biped", "other-biped"),
            encoding="utf-8",
        )
        other_assets = AssetLibrary(state_root, user_root=user_lib)
        other_assets.startup()
        durable_json(
            other_asset_dir / "validation.json",
            validator.run(
                other_asset_dir,
                "asset",
                "asset:robot/other-biped@1",
                other_assets.versions["robot/other-biped@1"].metadata,
                other_assets,
            ),
        )
        _write_validated_scene(
            state_root,
            user_lib,
            validator,
            name="biped-swapped-emb",
            devices_entities=patrol_entity_bind,
            waypoints={"patrol_north": {"p": [0.0, 1.2, 0.0], "yaw": 1.57}},
            scene_usda_text=_scene_usda("PatrolBiped").replace("robot/test-biped/1", "robot/other-biped/1"),
            asset_id="robot/other-biped@1",
            asset_dir=other_asset_dir,
        )
        emb_path = user_lib / "embodiments/test-biped.yaml"
        original_emb_yaml = emb_path.read_text(encoding="utf-8")
        emb_path.write_text(
            original_emb_yaml.replace("robot/test-biped@1", "robot/other-biped@1"), encoding="utf-8"
        )
        try:
            manager.library.refresh()
            swapped_scene = manager.library.resolve("biped-swapped-emb@1", launch=True, source="newton")
            with pytest.raises(PlanError) as exc_swapped:
                authorize_cross_scene_policy_deployment(swapped_scene, state_root=state_root)
            assert exc_swapped.value.detail["code"] == "deployment_authorization_required"

            swapped_eff = swapped_scene.report()["effectiveBindings"]["newton"][0]
            swapped_cat = compile_catalog(
                "rev-swapped",
                load_entity_types(),
                {"/World/Lab/PatrolBiped": swapped_eff},
                {"/World/Lab/PatrolBiped": BipedPolicyProgram.Config.model_validate(swapped_eff["config"])},
            )
            with pytest.raises(LibraryError) as exc_swapped_rt:
                verify_runtime_deployment(swapped_scene, swapped_cat, cross_permit, state_root=state_root)
            assert exc_swapped_rt.value.detail["code"] == "simulation_deployment_changed"
        finally:
            emb_path.write_text(original_emb_yaml, encoding="utf-8")


def test_builtin_reference_policy_permit_rsi_rounds_and_fresh_user_cross_scene_reuse(
    tmp_path, monkeypatch
):
    import asyncio

    from apps.api.app.sources.base import SpatialSource
    from packages.library import common as library_common
    from packages.library.scenes import SceneLibrary
    from packages.programs.deployment import authorize_builtin_policy_deployment
    from packages.protocol.schema import (
        BodyNode,
        SceneManifest,
        SourceInfo,
        TransformFrame,
    )
    from packages.training import deployment_authority

    builtin_lib = tmp_path / "builtin-library"
    user_lib = tmp_path / "user-library"
    state_root = tmp_path / "state"
    builtin_lib.mkdir(parents=True)
    user_lib.mkdir(parents=True)

    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))
    monkeypatch.setattr(library_common, "BUILTIN_ROOT", builtin_lib)
    monkeypatch.setattr(deployment_authority, "BUILTIN_ROOT", builtin_lib)
    monkeypatch.setattr(AssetLibrary.__init__, "__defaults__", (builtin_lib, None))
    monkeypatch.setattr(SceneLibrary.__init__, "__defaults__", (builtin_lib, None, None))

    validator = ControlledPublicationValidator(state_root / "val", program_schemas)

    # 1. Author built-in robot/builtin-biped@1 USD asset
    asset_dir = builtin_lib / "assets/robot/builtin-biped/1"
    asset_dir.mkdir(parents=True)
    raw_usda = BIPED_ASSET_USDA.encode("utf-8")
    (asset_dir / "asset.usda").write_bytes(raw_usda)
    (asset_dir / "LICENSE.txt").write_text("Apache-2.0\n", encoding="utf-8")
    durable_json(
        asset_dir / "provenance.json",
        {
            "source": "builtin-biped-fixture",
            "files": {"asset.usda": {"sha256": hashlib.sha256(raw_usda).hexdigest(), "bytes": len(raw_usda)}},
        },
    )
    (asset_dir / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "robot/builtin-biped",
                "kind": "robot",
                "title": "Built-in Reference Biped",
                "stage": "asset.usda",
                "physics": {"massKg": 2.4, "massSource": "measured", "collision": "authored"},
                "implements": [
                    {
                        "type": "biped/v1",
                        "programs": {"newton": BipedPostureProgram.id},
                        "config": {
                            "jointKeys": ["LeftHip", "RightHip", "LeftKnee", "RightKnee"],
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
    boot_assets = AssetLibrary(state_root, builtin_root=builtin_lib, user_root=user_lib)
    boot_assets.startup()
    durable_json(
        asset_dir / "validation.json",
        validator.run(
            asset_dir,
            "asset",
            "asset:robot/builtin-biped@1",
            boot_assets.versions["robot/builtin-biped@1"].metadata,
            boot_assets,
        ),
    )

    def _builtin_scene_usda(prim_name: str) -> str:
        return _scene_usda(prim_name).replace("robot/test-biped/1", "robot/builtin-biped/1")

    # 2. Built-in Scene @1 (posture baseline)
    _write_validated_scene(
        state_root,
        builtin_lib,
        validator,
        name="builtin-arena",
        rev=1,
        entity_prim="ArenaBiped",
        devices_entities=[
            {
                "key": "/World/Lab/ArenaBiped",
                "template": "robot/builtin-biped@1",
                "config": {"locations": "locations.yaml"},
            }
        ],
        waypoints={"arena_wp": {"p": [1.0, 0.0, 0.0], "yaw": 0.0}},
        scene_usda_text=_builtin_scene_usda("ArenaBiped"),
        asset_id="robot/builtin-biped@1",
        asset_dir=asset_dir,
    )
    arena_v1_hash = content_hash(builtin_lib / "scenes/builtin-arena/1")

    # 3. Built-in Reference policies (walkTo, standUp, bow)
    builtin_policies = _make_biped_policies(
        builtin_lib, robot="/World/Lab/ArenaBiped", scene="builtin-arena@1"
    )
    for pol_id in builtin_policies.values():
        pol_name, pol_rev = pol_id.removeprefix("policy/").split("@")
        _pin_policy_scene_hash(builtin_lib, pol_name, int(pol_rev), "builtin-arena@1", arena_v1_hash)

    # 4. Built-in Scene @2 (Reference multi-policy scene) & built-in patrol Scene @1
    arena_v2_entity = {
        "key": "/World/Lab/ArenaBiped",
        "type": "biped/v1",
        "program": BipedPolicyProgram.id,
        "config": {
            "jointKeys": [
                "/World/Lab/ArenaBiped/LeftHip",
                "/World/Lab/ArenaBiped/RightHip",
                "/World/Lab/ArenaBiped/LeftKnee",
                "/World/Lab/ArenaBiped/RightKnee",
            ],
            "baseBody": "/World/Lab/ArenaBiped/Trunk",
            "defaultPosition": [0.0, 0.0, 0.0, 0.0],
            "locations": "locations.yaml",
            "policies": builtin_policies,
        },
    }
    _write_validated_scene(
        state_root,
        builtin_lib,
        validator,
        name="builtin-arena",
        rev=2,
        entity_prim="ArenaBiped",
        devices_entities=[arena_v2_entity],
        waypoints={"arena_wp": {"p": [1.0, 0.0, 0.0], "yaw": 0.0}},
        scene_usda_text=_builtin_scene_usda("ArenaBiped"),
        asset_id="robot/builtin-biped@1",
        asset_dir=asset_dir,
    )
    _write_validated_scene(
        state_root,
        builtin_lib,
        validator,
        name="builtin-patrol",
        rev=1,
        entity_prim="PatrolUnit",
        devices_entities=[
            {
                "key": "/World/Lab/PatrolUnit",
                "type": "biped/v1",
                "program": BipedPolicyProgram.id,
                "config": {
                    "jointKeys": [
                        "/World/Lab/PatrolUnit/LeftHip",
                        "/World/Lab/PatrolUnit/RightHip",
                        "/World/Lab/PatrolUnit/LeftKnee",
                        "/World/Lab/PatrolUnit/RightKnee",
                    ],
                    "baseBody": "/World/Lab/PatrolUnit/Trunk",
                    "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                    "locations": "locations.yaml",
                    "policies": builtin_policies,
                },
            }
        ],
        waypoints={"checkpoint_a": {"p": [0.8, 0.4, 0.0], "yaw": 0.5}},
        scene_usda_text=_builtin_scene_usda("PatrolUnit"),
        asset_id="robot/builtin-biped@1",
        asset_dir=asset_dir,
    )

    # 5. Built-in Embodiment, RobotModel, and Controller v1
    for subdir in ("embodiments", "robot-models", "controllers/builtin-biped-ctrl"):
        (builtin_lib / subdir).mkdir(parents=True, exist_ok=True)
    (builtin_lib / "embodiments/builtin-biped.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "builtin-biped",
                "title": "Built-in Biped",
                "kind": "sim",
                "asset": "robot/builtin-biped@1",
                "controller": "builtin-biped-ctrl",
                "bindings": [
                    {"scene": "builtin-arena@1", "entity": "/World/Lab/ArenaBiped", "source": "newton"}
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (builtin_lib / "robot-models/builtin-biped-ref.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "builtin-biped-ref",
                "title": "Built-in Biped Reference RL Bundle",
                "family": "rl-policy",
                "source": builtin_policies["walkTo"],
                "license": "Apache-2.0",
                "parameters": 64,
                "embodiments": ["builtin-biped"],
                "upstreamEmbodiments": [],
                "actionSpace": "joint_position_offset",
                "compute": {"inference": "local CPU"},
                "runnable": True,
                "capability": {
                    "program": BipedPolicyProgram.id,
                    "sources": ["newton"],
                    "requirements": ["Built-in Reference Controller"],
                    "notes": "Reference multi-policy bundle",
                },
                "quality": {"status": "not-evaluated", "evaluationId": None},
                "sources": [
                    {
                        "title": "Spatial Sync Reference",
                        "url": "https://github.com/CaiZongyuan/spatial-sync/issues/279",
                        "retrievedOn": "2026-11-12",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (builtin_lib / "controllers/builtin-biped-ctrl/controller.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "builtin-biped-ctrl",
                "title": "Built-in Biped Controller",
                "embodiment": "builtin-biped",
                "currentVersion": 1,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    from packages.policies.library import PolicyLibrary

    builtin_pol_lib = PolicyLibrary({"builtin": builtin_lib})
    ctrl_policies_doc = {}
    for act, pid in sorted(builtin_policies.items()):
        rec, _ = builtin_pol_lib.resolve(pid)
        ctrl_policies_doc[act] = {
            "policy": pid,
            "action": {
                "space": "joint_position_offset",
                "hz": 20.0,
                "hzSource": pid + "/task.json#/control/hz",
            },
            "observation": rec["observationSha256"],
            "task": rec["task"],
        }
    walk_rec, _ = builtin_pol_lib.resolve(builtin_policies["walkTo"])
    (builtin_lib / "controllers/builtin-biped-ctrl/1.yaml").write_text(
        yaml.safe_dump(
            {
                "controller": "builtin-biped-ctrl",
                "version": 1,
                "embodiment": "builtin-biped",
                "robotModel": "builtin-biped-ref",
                "policy": builtin_policies["walkTo"],
                "program": BipedPolicyProgram.id,
                "programArtifact": None,
                "action": ctrl_policies_doc["walkTo"]["action"],
                "observation": walk_rec["observationSha256"],
                "task": walk_rec["task"],
                "policies": ctrl_policies_doc,
                "scene": "builtin-arena@2",
                "evaluationId": None,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    # Fresh empty state_root (simulating a fresh repo clone with ZERO local promotions!)
    fresh_state = tmp_path / "fresh-state"
    fresh_state.mkdir(parents=True)

    scenes = SceneLibrary(builtin_root=builtin_lib, user_root=user_lib, state_root=fresh_state)
    builtin_arena_2 = scenes.resolve("builtin-arena@2", launch=True, source="newton")
    builtin_patrol_1 = scenes.resolve("builtin-patrol@1", launch=True, source="newton")

    # A. Built-in permit issued and verified on BOTH base scene and built-in cross-scene patrol scene!
    arena_permit = authorize_builtin_policy_deployment(builtin_arena_2, state_root=fresh_state)
    assert arena_permit["schema"] == "lw.builtin-deployment-permit/1"
    assert arena_permit["controller"]["id"] == "builtin-biped-ctrl"
    assert arena_permit["controller"]["version"] == 1
    assert arena_permit["scene"] == {"id": "builtin-arena@2", "contentHash": builtin_arena_2.content_hash}

    patrol_permit = authorize_builtin_policy_deployment(builtin_patrol_1, state_root=fresh_state)
    assert patrol_permit == arena_permit

    for target_scene, entity_key in (
        (builtin_arena_2, "/World/Lab/ArenaBiped"),
        (builtin_patrol_1, "/World/Lab/PatrolUnit"),
    ):
        eff = target_scene.report()["effectiveBindings"]["newton"][0]
        cat = compile_catalog(
            "rev-b",
            load_entity_types(),
            {entity_key: eff},
            {entity_key: BipedPolicyProgram.Config.model_validate(eff["config"])},
        )
        verified = verify_runtime_deployment(target_scene, cat, arena_permit, state_root=fresh_state)
        assert verified.permit == arena_permit

        # Tampering wire permit field MUST be rejected by Runner verification!
        forged_permit = deepcopy(arena_permit)
        forged_permit["target"]["action"] = "bow"
        with pytest.raises(LibraryError):
            verify_runtime_deployment(target_scene, cat, forged_permit, state_root=fresh_state)

    # B. Fresh-clone zero-shot cross-scene reuse in a user-authored scene (empty state_root!)
    _write_validated_scene(
        fresh_state,
        user_lib,
        validator,
        name="user-patrol",
        rev=1,
        entity_prim="UserBiped",
        devices_entities=[
            {
                "key": "/World/Lab/UserBiped",
                "type": "biped/v1",
                "program": BipedPolicyProgram.id,
                "config": {
                    "jointKeys": [
                        "/World/Lab/UserBiped/LeftHip",
                        "/World/Lab/UserBiped/RightHip",
                        "/World/Lab/UserBiped/LeftKnee",
                        "/World/Lab/UserBiped/RightKnee",
                    ],
                    "baseBody": "/World/Lab/UserBiped/Trunk",
                    "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                    "locations": "locations.yaml",
                    "policies": builtin_policies,
                },
            }
        ],
        waypoints={"user_wp": {"p": [0.6, -0.3, 0.0], "yaw": -0.4}},
        scene_usda_text=_builtin_scene_usda("UserBiped"),
        asset_id="robot/builtin-biped@1",
        asset_dir=asset_dir,
    )
    scenes.refresh()
    user_patrol_1 = scenes.resolve("user-patrol@1", launch=True, source="newton")
    user_cross_permit = authorize_cross_scene_policy_deployment(user_patrol_1, state_root=fresh_state)
    assert user_cross_permit == arena_permit
    user_eff = user_patrol_1.report()["effectiveBindings"]["newton"][0]
    user_cat = compile_catalog(
        "rev-u",
        load_entity_types(),
        {"/World/Lab/UserBiped": user_eff},
        {"/World/Lab/UserBiped": BipedPolicyProgram.Config.model_validate(user_eff["config"])},
    )
    assert (
        verify_runtime_deployment(user_patrol_1, user_cat, user_cross_permit, state_root=fresh_state).permit
        == arena_permit
    )

    # C. Gateway SessionManager + GET /api/v1/embodiments/{id} activeSessions + ControllerPromotions RSI round
    class FakeNewtonRunnerSource(SpatialSource):
        def __init__(self, session_id: str):
            self.session_id = session_id
            self.scene_version = None
            self.state_root = None
            self.deployment_permit = None
            self._frame = None
            self._closed = asyncio.Event()

        def configure_scene(self, scene_version):
            self.scene_version = scene_version

        def configure_execution(self, *, state_root, deployment_permit=None):
            self.state_root = state_root
            self.deployment_permit = deepcopy(deployment_permit)

        async def start(self, scene_id: str, options):
            eff = self.scene_version.report()["effectiveBindings"]["newton"][0]
            ekey = eff["key"]
            catalog = compile_catalog(
                "rev-session",
                load_entity_types(),
                {ekey: eff},
                {ekey: BipedPolicyProgram.Config.model_validate(eff["config"])},
            )
            assert self.deployment_permit is not None
            verify_runtime_deployment(
                self.scene_version, catalog, self.deployment_permit, state_root=self.state_root
            )
            manifest = SceneManifest(
                sessionId=self.session_id,
                sceneRevision="rev-session",
                revisionNumericId=1,
                sceneName=self.scene_version.name,
                source=SourceInfo(kind="newton", physicsDt=options.physicsDt),
                nodes=[
                    BodyNode(
                        entityKey=f"{ekey}/Trunk",
                        name="Trunk",
                        streamId=1,
                    )
                ],
                sites=[],
                joints=[],
            )
            self._frame = TransformFrame(
                sessionId=self.session_id,
                sceneRevision="rev-session",
                epoch=0,
                seq=1,
                simTime=0.0,
                sampleTimeMs=1.0,
                poses=[(1, 0.0, 0.0, 0.24, 0.0, 0.0, 0.0, 1.0)],
            )
            return manifest, catalog

        def latest_frame(self):
            return self._frame

        async def snapshot(self):
            return self._frame

        async def events(self):
            await self._closed.wait()
            if False:
                yield None

        async def execute(self, command):
            raise NotImplementedError

        async def close(self):
            self._closed.set()
            return None

    def fake_factory(kind: str, session_id: str):
        assert kind == "newton"
        return FakeNewtonRunnerSource(session_id)

    settings = Settings(state_root=fresh_state, recordings_root=tmp_path / "rec", node_token=TOKEN)
    app = create_app(settings, source_factory=fake_factory, controller_publication_validator=validator)
    with TestClient(app) as client:
        sess_res = client.post(
            "/api/v1/sessions",
            json={
                "sceneVersion": "builtin-arena@2",
                "source": "newton",
                "continueWithoutObservers": True,
            },
        )
        assert sess_res.status_code in (200, 201), sess_res.text
        session_id = sess_res.json()["sessionId"]

        emb_res = client.get("/api/v1/embodiments/builtin-biped")
        assert emb_res.status_code == 200, emb_res.text
        active = emb_res.json()["activeSessions"]
        assert len(active) == 1
        assert active[0]["sessionId"] == session_id
        assert active[0]["scene"] == "builtin-arena@2"
        assert active[0]["controller"]["id"] == "builtin-biped-ctrl"
        assert active[0]["controller"]["version"] == 1

        del_res = client.delete(f"/api/v1/sessions/{session_id}")
        assert del_res.status_code in (200, 204), del_res.text

        # Verify ControllerPromotions.authorize_scene, notebook_default, _controller, _materials, and RSI v2!
        manager = client.app.state.manager
        promotions = client.app.state.controller_promotions
        resolved_b2 = manager.library.resolve("builtin-arena@2", launch=True, source="newton")
        assert promotions.authorize_scene(resolved_b2) == arena_permit

        nb_def = promotions.notebook_default(
            ("builtin-arena@2", resolved_b2.content_hash, "newton", "/World/Lab/ArenaBiped")
        )
        assert nb_def is not None
        assert nb_def["status"] == "committed"
        assert nb_def["controller"]["id"] == "builtin-biped-ctrl"
        assert nb_def["controller"]["version"] == 1

        # Promote v2 on top of built-in Reference scene builtin-arena@2!
        walk_pkg = builtin_lib / "policies/biped-walk/1/package"
        user_walk_task = yaml.safe_load((walk_pkg / "task.json").read_text(encoding="utf-8")) | {
            "name": "user-walk",
            "scene": "builtin-arena@2",
        }
        user_walk_id = write_policy_package(
            user_lib,
            name="user-walk",
            rev=1,
            task=user_walk_task,
            spec=yaml.safe_load((walk_pkg / "obs_spec.yaml").read_text(encoding="utf-8")),
            onnx_bytes=(walk_pkg / "artifacts/policy.onnx").read_bytes(),
        )
        _pin_policy_scene_hash(user_lib, "user-walk", 1, "builtin-arena@2", resolved_b2.content_hash)

        suite_dir = user_lib / "suites/builtin-walk-suite/1"
        (suite_dir / "private").mkdir(parents=True, exist_ok=True)
        (suite_dir / "task.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "builtin-walk-suite",
                    "scene": "builtin-arena@2",
                    "robot": "/World/Lab/ArenaBiped",
                    "implements": "biped/v1.walkTo",
                    "timeoutSeconds": 5.0,
                    "success": {"entity": "/World/Lab/ArenaBiped", "property": "tiltDeg", "lte": 50.0},
                    "milestones": [
                        {
                            "id": "goal",
                            "when": {"entity": "/World/Lab/ArenaBiped", "property": "tiltDeg", "lte": 50.0},
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
                    "id": "suite/builtin-walk-suite",
                    "title": "Walk Suite",
                    "layer": "embodied",
                    "source": "newton",
                    "scene": "builtin-arena@2",
                    "sceneContentHash": resolved_b2.content_hash,
                    "episodes": [
                        {"id": "train-1", "split": "train", "params": {"waypoint": "arena_wp"}},
                        {"id": "test-1", "split": "test", "params": {"waypoint": "arena_wp"}},
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        suites = SuiteLibrary(manager.library_assets.roots)
        suites.refresh()
        suite = suites.resolve("suite/builtin-walk-suite@1")
        subject = resolve_policy_subject(manager, user_walk_id, suite, resolved_b2)
        emb, ctrl = promotions._controller(resolved_b2, "/World/Lab/ArenaBiped")
        assert emb["id"] == "builtin-biped"
        assert ctrl["id"] == "builtin-biped-ctrl"
        assert ctrl["currentVersion"] == 1

        materials = promotions._materials(resolved_b2, subject)
        assert materials["/World/Lab/ArenaBiped"]["kind"] == "policy-bundle"
        assert materials["/World/Lab/ArenaBiped"]["policies"]["walkTo"]["id"] == user_walk_id
        assert materials["/World/Lab/ArenaBiped"]["policies"]["standUp"]["id"] == builtin_policies["standUp"]

        model = promotions._model(
            subject, emb, {"evaluationId": "eval-r2", "at": "2026-11-12T00:00:00Z"}
        )
        durable_json(user_lib / f"robot-models/{model['id']}.yaml", model)
        v2_comp = deepcopy(ctrl_policies_doc) | {"walkTo": promotions._policy_composition(user_walk_id)}
        committed_v2 = _publish_promotion(
            manager=manager,
            robot_resources=client.app.state.robot_resources,
            validator=validator,
            state_root=fresh_state,
            user_lib=user_lib,
            promotion_id="promotion-builtin-r2",
            scene=resolved_b2,
            subject=subject,
            controller=ctrl,
            version_fields={
                "embodiment": emb["id"],
                "robotModel": model["id"],
                "policy": user_walk_id,
                "program": BipedPolicyProgram.id,
                "action": v2_comp["walkTo"]["action"],
                "observation": subject.candidateIdentity.observationSha256,
                "task": subject.candidateIdentity.task,
                "policies": v2_comp,
                "evaluationId": "eval-r2",
            },
            materials=materials,
        )
        assert committed_v2["scene"]["id"] == "builtin-arena@3"
        assert committed_v2["controllerVersion"] == 2
        assert committed_v2["controller"]["document"]["version"] == 2

        # Once superseded in user/controllers (v2), stale v1 user-patrol@1 is no longer authorized,
        # while user-patrol@2 binding the promoted v2 composition IS authorized!
        manager.library.refresh()
        stale_user_patrol = manager.library.resolve("user-patrol@1", launch=True, source="newton")
        with pytest.raises(PlanError):
            authorize_cross_scene_policy_deployment(stale_user_patrol, state_root=fresh_state)

        _write_validated_scene(
            fresh_state,
            user_lib,
            validator,
            name="user-patrol",
            rev=2,
            entity_prim="UserBiped",
            devices_entities=[
                {
                    "key": "/World/Lab/UserBiped",
                    "type": "biped/v1",
                    "program": BipedPolicyProgram.id,
                    "config": {
                        "jointKeys": [
                            "/World/Lab/UserBiped/LeftHip",
                            "/World/Lab/UserBiped/RightHip",
                            "/World/Lab/UserBiped/LeftKnee",
                            "/World/Lab/UserBiped/RightKnee",
                        ],
                        "baseBody": "/World/Lab/UserBiped/Trunk",
                        "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                        "locations": "locations.yaml",
                        "policies": builtin_policies | {"walkTo": user_walk_id},
                    },
                }
            ],
            waypoints={"user_wp": {"p": [0.6, -0.3, 0.0], "yaw": -0.4}},
            scene_usda_text=_builtin_scene_usda("UserBiped"),
            asset_id="robot/builtin-biped@1",
            asset_dir=asset_dir,
        )
        manager.library.refresh()
        v2_user_patrol = manager.library.resolve("user-patrol@2", launch=True, source="newton")
        v2_permit = authorize_cross_scene_policy_deployment(v2_user_patrol, state_root=fresh_state)
        assert v2_permit["schema"] == "lw.deployment-permit/1"
        assert v2_permit["controller"]["version"] == 2

        # Shadowing builtin-biped in user/embodiments after v2 promotion MUST also fail closed!
        shadow_emb = user_lib / "embodiments/builtin-biped.yaml"
        shadow_emb.parent.mkdir(parents=True, exist_ok=True)
        shadow_emb.write_text(
            (builtin_lib / "embodiments/builtin-biped.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        try:
            with pytest.raises(PlanError) as exc_shadow:
                authorize_cross_scene_policy_deployment(v2_user_patrol, state_root=fresh_state)
            assert exc_shadow.value.detail["code"] == "deployment_authorization_required"

            v2_eff = v2_user_patrol.report()["effectiveBindings"]["newton"][0]
            v2_cat = compile_catalog(
                "rev-v2-shadow",
                load_entity_types(),
                {"/World/Lab/UserBiped": v2_eff},
                {"/World/Lab/UserBiped": BipedPolicyProgram.Config.model_validate(v2_eff["config"])},
            )
            with pytest.raises(LibraryError) as exc_shadow_rt:
                verify_runtime_deployment(v2_user_patrol, v2_cat, v2_permit, state_root=fresh_state)
            assert exc_shadow_rt.value.detail["code"] == "simulation_deployment_changed"
        finally:
            shadow_emb.unlink()
