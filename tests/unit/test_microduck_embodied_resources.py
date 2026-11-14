"""GPU-free unit tests for Wave-4 Microduck Tasks, NativeProfiles, Policies, RobotResources, and @2 Scenes."""

from __future__ import annotations

import ast
import io
import shutil
from pathlib import Path

import numpy as np
import pytest

from apps.api.app.library_validation import program_schemas
from apps.node.observation_capture import code_hash
from apps.node.usd_mjcf import derive_usd_mjcf, verify_against_observation_spec
from packages.library.assets import AssetLibrary
from packages.library.common import BUILTIN_ROOT, document
from packages.library.previews import PreviewRepository
from packages.library.robot_resources import RobotResources
from packages.library.scenes import SceneLibrary
from packages.library.tasks import TaskLibrary
from packages.policies.inference import CpuPolicy
from packages.policies.library import PolicyLibrary, read_json
from packages.policies.observations import ObservationSpec
from packages.policies.qualification import NativeProfiles
from packages.programs.deployment import authorize_builtin_policy_deployment

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TASK_REFS = (
    "task/microduck-bow@1",
    "task/microduck-stand-up@1",
    "task/microduck-walk-to@1",
)
EXPECTED_POLICY_REFS = (
    "policy/microduck-bow@1",
    "policy/microduck-stand-up@1",
    "policy/microduck-walk-to@1",
)


def _assert_method_in_ast(py_path: Path, qualname: str) -> None:
    class_name, method_name = qualname.split(".")
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
            assert method_name in methods, f"Missing {method_name} on {class_name} in {py_path}"
            return
    raise AssertionError(f"Missing class {class_name} in {py_path}")


def test_microduck_builtin_tasks_list_show_validate_push_and_usd_parity(tmp_path: Path) -> None:
    assets = AssetLibrary(
        tmp_path / "state",
        builtin_root=BUILTIN_ROOT,
        user_root=tmp_path / "user",
    )
    assets.startup()
    scenes = SceneLibrary(
        builtin_root=BUILTIN_ROOT,
        user_root=tmp_path / "user",
        state_root=tmp_path / "state",
    )
    arena_v1 = scenes.resolve("microduck-arena@1", launch=True, source="newton")
    tasks = TaskLibrary(assets, lambda: scenes)

    listed_ids = tuple(item["id"] for item in tasks.list())
    assert EXPECTED_TASK_REFS == tuple(tid for tid in listed_ids if "microduck" in tid)

    derived_scene = derive_usd_mjcf(
        BUILTIN_ROOT / "scenes/microduck-arena/1/stage.usda",
        prim_path="/World/Lab/Microduck",
        asset_packages={"robot/microduck@1": BUILTIN_ROOT / "assets/robot/microduck/1"},
    )

    # Also build an isolated builtin root without tasks/ to verify non-idempotent TaskLibrary.push()
    isolated_builtin = tmp_path / "isolated_builtin"
    isolated_builtin.mkdir()
    for subdir in ("assets", "scenes", "suites"):
        shutil.copytree(BUILTIN_ROOT / subdir, isolated_builtin / subdir)
    iso_assets = AssetLibrary(
        tmp_path / "iso_state",
        builtin_root=isolated_builtin,
        user_root=tmp_path / "iso_user",
    )
    iso_assets.startup()
    iso_scenes = SceneLibrary(
        builtin_root=isolated_builtin,
        user_root=tmp_path / "iso_user",
        state_root=tmp_path / "iso_state",
    )
    iso_tasks = TaskLibrary(iso_assets, lambda: iso_scenes)

    for task_id in EXPECTED_TASK_REFS:
        shown = tasks.show(task_id)
        assert shown["sceneContentHash"] == arena_v1.content_hash
        task_doc = shown["task"]
        assert task_doc["scene"] == "microduck-arena@1"
        assert task_doc["robot"] == "/World/Lab/Microduck"
        assert task_doc["control"] == {"action": "joint_position_offset", "hz": 50.0}
        assert task_doc["mjlab"]["sceneInput"] == "usd"
        assert task_doc["mjlab"]["physics_dt"] == pytest.approx(0.002)
        assert task_doc["mjlab"]["decimation"] == 10

        pkg_dir = BUILTIN_ROOT / "tasks" / shown["name"] / "1/package"
        validated_doc, validated_scene = tasks._validate(pkg_dir)
        assert validated_doc["name"] == shown["name"]
        assert validated_scene.content_hash == arena_v1.content_hash

        # Verify pure-USD observation/action parity against canonical Microduck USD stage
        obs_doc = document(pkg_dir / task_doc["observation"])
        verify_against_observation_spec(
            derived_scene,
            obs_doc,
            expected_kp=[0.55] * 14,
            expected_kd=[0.053] * 14,
            check_observation_offsets=True,
        )

        # Verify TaskLibrary.push() idempotent against BUILTIN_ROOT and fresh against isolated user_root
        archive_bytes = tasks.export(task_id)
        idem = tasks.push(io.BytesIO(archive_bytes))
        assert idem["id"] == task_id
        assert idem["idempotent"] is True
        assert idem["contentHash"] == shown["contentHash"]

        fresh = iso_tasks.push(io.BytesIO(archive_bytes))
        assert fresh["id"] == task_id
        assert fresh["idempotent"] is False
        assert fresh["contentHash"] == shown["contentHash"]
        assert fresh["sceneContentHash"] == arena_v1.content_hash


def test_microduck_native_profiles_match_tasks_and_state_providers(tmp_path: Path) -> None:
    """Runs unconditionally in GPU-free CI without mjlab/mujoco/newton installed."""
    assets = AssetLibrary(
        tmp_path / "state",
        builtin_root=BUILTIN_ROOT,
        user_root=tmp_path / "user",
    )
    assets.startup()
    scenes = SceneLibrary(
        builtin_root=BUILTIN_ROOT,
        user_root=tmp_path / "user",
        state_root=tmp_path / "state",
    )
    tasks = TaskLibrary(assets, lambda: scenes)
    profiles = NativeProfiles()

    for name in ("microduck-walk-to", "microduck-stand-up", "microduck-bow"):
        task_rec = tasks.show(f"task/{name}@1")
        profile = profiles.require(f"{name}-mjlab")
        assert profile.evidenceMode == "native"
        assert profile.taskContentHash == task_rec["contentHash"]
        assert profile.actionWidth == 14
        assert profile.inputName == "obs"

        matched = profiles.for_task(task_rec["contentHash"], profile.versions["rsl_rl"])
        assert matched is not None and matched["id"] == f"{name}-mjlab"

        pkg_dir = BUILTIN_ROOT / "tasks" / name / "1/package"
        spec = ObservationSpec.model_validate(document(pkg_dir / "obs_spec.yaml"))
        assert profile.inputWidth == spec.size

        provider_file = pkg_dir / profile.stateProviderFile
        assert provider_file.is_file()
        assert code_hash(provider_file) == profile.stateProvider.sha256
        _assert_method_in_ast(provider_file, profile.stateProvider.qualname)


def test_microduck_builtin_policies_resolve_and_cpu_onnx_infer() -> None:
    policies = PolicyLibrary({"builtin": BUILTIN_ROOT})
    listed = [item["id"] for item in policies.list() if "microduck" in item["id"]]
    assert tuple(listed) == EXPECTED_POLICY_REFS

    rng = np.random.default_rng(7)
    for policy_id in EXPECTED_POLICY_REFS:
        rec, pkg = policies.resolve(policy_id)
        assert rec["scene"] == "microduck-arena@1"
        assert rec["control"] == {"hz": 50.0, "action": "joint_position_offset"}
        onnx_path = pkg / rec["onnx"]
        assert onnx_path.is_file()
        assert onnx_path.stat().st_size < 300 * 1024

        spec = ObservationSpec.model_validate(document(pkg / "obs_spec.yaml"))
        export_doc = read_json(pkg / "artifacts/export.json")
        cpu_pol = CpuPolicy(onnx_path, spec, root=pkg / "artifacts", external=export_doc["externalData"])
        for _ in range(4):
            vec = rng.standard_normal(spec.size).astype(np.float32) * 0.1
            out = cpu_pol.infer(vec)
            assert out.shape == (14,)
            assert np.isfinite(out).all()


def test_microduck_robot_resources_default_v2_scenes_previews_and_permits(tmp_path: Path) -> None:
    empty_state = tmp_path / "empty_state"
    empty_user = tmp_path / "empty_user"
    assets = AssetLibrary(empty_state, builtin_root=BUILTIN_ROOT, user_root=empty_user)
    assets.startup()
    scenes = SceneLibrary(builtin_root=BUILTIN_ROOT, user_root=empty_user, state_root=empty_state)
    previews = PreviewRepository(tmp_path / "preview-cache")

    # 1. Versionless resolution defaults to @2, while @1 remains untouched on biped.posture
    arena_default = scenes.resolve("microduck-arena", launch=True, source="newton")
    patrol_default = scenes.resolve("microduck-lab-patrol", launch=True, source="newton")
    assert arena_default.id == "microduck-arena@2"
    assert patrol_default.id == "microduck-lab-patrol@2"

    arena_v1 = scenes.resolve("microduck-arena@1", launch=True, source="newton")
    patrol_v1 = scenes.resolve("microduck-lab-patrol@1", launch=True, source="newton")
    assert arena_v1.declarations("newton").declarations[0].binding["program"] == "biped.posture"
    patrol_v1_by_key = {
        d.binding["key"]: d.binding for d in patrol_v1.declarations("newton").declarations
    }
    assert patrol_v1_by_key["/World/Lab/PatrolDuck"]["program"] == "biped.posture"

    # 2. Both @2 scenes use derivedFrom (NO promotion key), pass validation, and ship static previews
    for v2_scene, parent_id, bot_key in (
        (arena_default, "microduck-arena@1", "/World/Lab/Microduck"),
        (patrol_default, "microduck-lab-patrol@1", "/World/Lab/PatrolDuck"),
    ):
        raw_scene_yaml = document(v2_scene.path / "scene.yaml")
        assert "promotion" not in raw_scene_yaml
        assert raw_scene_yaml["derivedFrom"] == {"scene": parent_id}

        rep = v2_scene.report()
        assert rep["launchable"] is True
        all_checks = [*rep["checks"], *(rep.get("physics") or {}).get("checks", [])]
        assert all(item["status"] != "fail" for item in all_checks), rep
        for cid in ("S1", "S2", "S3", "S5", "S7", "P1", "P2", "P5"):
            matched = [item["status"] for item in all_checks if item["id"] == cid]
            assert matched and all(st == "pass" for st in matched), (v2_scene.id, cid)
        p3_checks = [item for item in all_checks if item["id"] == "P3"]
        assert len(p3_checks) == 1 and p3_checks[0]["status"] in ("pass", "warn")
        assert p3_checks[0]["measurements"]["depthM"] <= p3_checks[0]["measurements"]["limitM"]

        by_key = {d.binding["key"]: d.binding for d in v2_scene.declarations("newton").declarations}
        assert by_key[bot_key]["program"] == "biped.policy"
        assert by_key[bot_key]["config"]["policies"] == {
            "walkTo": "policy/microduck-walk-to@1",
            "standUp": "policy/microduck-stand-up@1",
            "bow": "policy/microduck-bow@1",
        }
        assert previews.describe(v2_scene, "newton")["status"] == "available"

    all_builtin = scenes.list()
    assert len(all_builtin) == 12
    for summary in all_builtin:
        ver = scenes.resolve(summary["id"])
        primary_source = ver.metadata["sources"][0]
        desc = previews.describe(ver, primary_source)
        assert desc["status"] == "available", (summary["id"], primary_source, desc)

    # 3. RobotResources (Embodiment, RobotModel, Reference Controller)
    rr = RobotResources({"builtin": BUILTIN_ROOT}, assets, scenes, program_schemas())
    emb = rr.embodiment("microduck-sim")
    assert emb["kind"] == "sim"
    assert emb["asset"] == "robot/microduck@1"
    assert emb["controller"] == "microduck-biped"
    assert emb["currentController"]["version"] == 1
    bound_scenes = {(b["scene"], b["entity"], b["source"]) for b in emb["bindings"]}
    assert bound_scenes == {
        ("microduck-arena@1", "/World/Lab/Microduck", "newton"),
        ("microduck-arena@2", "/World/Lab/Microduck", "newton"),
        ("microduck-lab-patrol@1", "/World/Lab/PatrolDuck", "newton"),
        ("microduck-lab-patrol@2", "/World/Lab/PatrolDuck", "newton"),
    }

    mdl = rr.model("microduck-mjlab-ppo")
    assert mdl["family"] == "rl-policy"
    assert mdl["runnable"] is True
    assert mdl["compute"] == {"inference": "CPU"}
    assert mdl["quality"]["status"] == "evaluated"
    assert mdl["quality"]["evaluationId"].startswith("eval-")
    assert all(src["url"].startswith("https://") for src in mdl["sources"])

    ctrl = rr.controller("microduck-biped")
    assert ctrl["currentVersion"] == 1
    assert ctrl["current"]["scene"] == "microduck-arena@2"
    assert ctrl["current"]["policy"] == "policy/microduck-walk-to@1"
    assert set(ctrl["current"]["policies"]) == {"walkTo", "standUp", "bow"}

    # 4. Built-in deployment authorization succeeds with a completely empty state_root
    brand_new_state = tmp_path / "brand_new_empty_state"
    for v2_scene in (arena_default, patrol_default):
        permit = authorize_builtin_policy_deployment(v2_scene, state_root=brand_new_state)
        assert permit["schema"] == "lw.builtin-deployment-permit/1"


def test_microduck_mjmodel_parity_across_all_three_tasks() -> None:
    pytest.importorskip("mujoco")
    derived = derive_usd_mjcf(
        BUILTIN_ROOT / "scenes/microduck-arena/1/stage.usda",
        prim_path="/World/Lab/Microduck",
        asset_packages={"robot/microduck@1": BUILTIN_ROOT / "assets/robot/microduck/1"},
    )
    model = derived.to_mjmodel()
    assert model.nu == 14
    for name in ("microduck-walk-to", "microduck-stand-up", "microduck-bow"):
        obs_doc = document(BUILTIN_ROOT / "tasks" / name / "1/package/obs_spec.yaml")
        verify_against_observation_spec(
            derived,
            obs_doc,
            expected_kp=[0.55] * 14,
            expected_kd=[0.053] * 14,
            check_observation_offsets=True,
        )
