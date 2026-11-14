"""Unit verification for built-in Microduck USD asset, scenes, embodied suites, and MJCF derivation."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from apps.node.usd_mjcf import derive_usd_mjcf, verify_against_observation_spec
from packages.library.assets import AssetLibrary
from packages.library.common import BUILTIN_ROOT
from packages.library.previews import PreviewRepository
from packages.library.scenes import SceneLibrary
from packages.library.suites import SuiteLibrary
from tools.package_microduck_asset import PINNED_UPSTREAM_SHA256, verify_upstream_source

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_JOINTS = (
    "left_hip_yaw",
    "left_hip_roll",
    "left_hip_pitch",
    "left_knee",
    "left_ankle",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "right_hip_yaw",
    "right_hip_roll",
    "right_hip_pitch",
    "right_knee",
    "right_ankle",
)

EXPECTED_STAND2 = (
    0.0,
    -0.0873,
    -0.4579,
    -0.0049,
    0.453,
    0.3491,
    0.3491,
    0.0,
    0.0,
    0.0,
    0.0873,
    0.4579,
    0.0049,
    -0.453,
)

EXPECTED_BODY_MASSES = {
    "trunk_base": 0.199223995,
    "yaw2roll": 0.023040600,
    "hip_l": 0.006189340,
    "upper_leg_left": 0.048206698,
    "leg": 0.021584401,
    "ankle_left": 0.030024599,
    "neck": 0.036841400,
    "neck_pitch": 0.005720000,
    "yaw_roll_motion": 0.048599999,
    "jaw_soft": 0.188766003,
    "bearing_roll": 0.023040600,
    "hip_l_2": 0.006189340,
    "upper_leg_right": 0.048206698,
    "leg_2": 0.021584401,
    "ankle_right": 0.030025100,
}

SYNTHETIC_INTERMEDIATE_BODIES = (
    "yaw2roll",
    "bearing_roll",
    "upper_leg_left",
    "upper_leg_right",
    "neck",
    "neck_pitch",
    "yaw_roll_motion",
)

EXPECTED_SITES = (
    "imu",
    "left_foot",
    "head_camera",
    "tof",
    "head_imu",
    "mouth_tip",
    "right_foot",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_microduck_asset_package_validation_and_provenance(tmp_path: Path) -> None:
    assets = AssetLibrary(
        tmp_path / "state",
        builtin_root=BUILTIN_ROOT,
        user_root=tmp_path / "user",
    )
    assets.startup()
    version = assets.resolve("robot/microduck@1")
    report = version.report()
    assert report["launchable"] is True
    all_checks = [*report["checks"], *(report.get("physics") or {}).get("checks", [])]
    assert all(item["status"] != "fail" for item in all_checks), report
    for check_id in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "P1", "P2", "P3"):
        matching = [item["status"] for item in all_checks if item["id"] == check_id]
        assert matching and all(status == "pass" for status in matching), (check_id, report)
    p4_checks = [item for item in all_checks if item["id"] == "P4"]
    assert len(p4_checks) == 15 and all(item["status"] == "pass" for item in p4_checks)
    assert all(item["status"] == "present" for item in report["completeness"])

    license_text = (version.path / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text and "Version 2.0" in license_text

    provenance = json.loads((version.path / "provenance.json").read_text(encoding="utf-8"))
    producer_path = ROOT / "tools/package_microduck_asset.py"
    assert provenance["producer"] == {
        "path": "tools/package_microduck_asset.py",
        "sha256": _sha256(producer_path),
        "bytes": producer_path.stat().st_size,
    }
    assert provenance["license"].startswith("Apache-2.0")
    assert provenance["revision"] == "5984efb770855432b03dafd3d879e9929981e45b"
    for pinned_rel, pinned_sha in PINNED_UPSTREAM_SHA256.items():
        assert provenance["sourceFiles"][pinned_rel]["sha256"] == pinned_sha
    assert set(provenance["files"]) == {"LICENSE", "asset.usdc", "asset.yaml", "visual.glb"}
    for rel_name, entry in provenance["files"].items():
        target = version.path / rel_name
        assert target.is_file()
        assert entry["bytes"] == target.stat().st_size
        assert entry["sha256"] == _sha256(target)

    # Verify verify_upstream_source rejects tampered or unverified directories
    fake_src = tmp_path / "fake-upstream"
    fake_src.mkdir()
    shutil.copyfile(version.path / "LICENSE", fake_src / "LICENSE")
    for rel in PINNED_UPSTREAM_SHA256:
        if rel == "LICENSE":
            continue
        dest = fake_src / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_upstream_source(fake_src)

    impl = version.metadata["implements"][0]
    assert impl["type"] == "biped/v1"
    assert impl["programs"] == {"newton": "biped.posture"}
    cfg = impl["config"]
    assert cfg["baseBody"] == "trunk_base"
    assert tuple(cfg["jointKeys"]) == tuple(f"joints/{name}" for name in EXPECTED_JOINTS)
    assert tuple(cfg["defaultPosition"]) == pytest.approx(EXPECTED_STAND2, abs=1e-6)
    assert cfg["minStandHeight"] == pytest.approx(0.09)
    assert cfg["fallMinTiltDeg"] == pytest.approx(68.0)
    assert cfg["fallSettleSeconds"] == pytest.approx(0.45)
    assert set(cfg["actionTargets"]) == {"standUp", "bow"}
    assert set(cfg["fallPostures"]) == {"front", "back", "left", "right"}


def test_microduck_scenes_and_static_previews_are_launchable_and_available(tmp_path: Path) -> None:
    scenes = SceneLibrary(
        builtin_root=BUILTIN_ROOT,
        user_root=tmp_path / "user",
        state_root=tmp_path / "state",
    )
    previews = PreviewRepository(tmp_path / "preview-cache")

    arena = scenes.resolve("microduck-arena@1", launch=True, source="newton")
    arena_report = arena.report()
    assert arena_report["launchable"] is True
    assert arena.metadata["simulation"]["physicsDt"] == pytest.approx(0.002)
    assert set(arena.locations["waypoints"]) == {
        "origin",
        "north",
        "south",
        "east",
        "west",
        "northeast",
        "northwest",
        "turn_left",
        "turn_right",
    }
    arena_decls = {item.binding["key"]: item.binding for item in arena.declarations("newton").declarations}
    assert set(arena_decls) == {"/World/Lab/Microduck"}
    assert arena_decls["/World/Lab/Microduck"]["type"] == "biped/v1"
    assert previews.describe(arena, "newton")["status"] == "available"

    patrol = scenes.resolve("microduck-lab-patrol@1", launch=True, source="newton")
    patrol_report = patrol.report()
    assert patrol_report["launchable"] is True
    assert patrol.metadata["simulation"]["physicsDt"] == pytest.approx(0.002)
    assert set(patrol.locations["waypoints"]) == {
        "dock",
        "aisle_west",
        "bench_north",
        "centrifuge_bay",
        "corridor_east",
        "inspect_turn",
    }
    patrol_decls = {item.binding["key"]: item.binding for item in patrol.declarations("newton").declarations}
    assert set(patrol_decls) == {"/World/Lab/PatrolDuck", "/World/Lab/Centrifuge"}
    assert patrol_decls["/World/Lab/PatrolDuck"]["type"] == "biped/v1"
    assert patrol_decls["/World/Lab/Centrifuge"]["type"] == "centrifuge/v1"
    assert previews.describe(patrol, "newton")["status"] == "available"


def test_microduck_embodied_suites_bind_exact_arena_hash_and_hide_private_splits(tmp_path: Path) -> None:
    scenes = SceneLibrary(
        builtin_root=BUILTIN_ROOT,
        user_root=tmp_path / "user",
        state_root=tmp_path / "state",
    )
    arena = scenes.resolve("microduck-arena@1", launch=True, source="newton")
    suites = SuiteLibrary({"builtin": BUILTIN_ROOT, "user": tmp_path / "user"})
    suites.refresh()

    expected_implements = {
        "suite/microduck-walk-to@1": "biped/v1.walkTo",
        "suite/microduck-stand-up@1": "biped/v1.standUp",
        "suite/microduck-bow@1": "biped/v1.bow",
    }
    for suite_id, implements in expected_implements.items():
        suite = suites.resolve(suite_id)
        assert suite.metadata["layer"] == "embodied"
        assert suite.metadata["scene"] == "microduck-arena@1"
        assert suite.metadata["sceneContentHash"] == arena.content_hash
        assert suite.metadata["source"] == "newton"
        assert suite.task["robot"] == "/World/Lab/Microduck"
        assert suite.task["implements"] == implements
        success_clauses = suite.task["success"]["all"]
        assert {"property": "baseHeight", "gte": 0.09} in success_clauses
        assert {"property": "tiltDeg", "lte": 20.0} in success_clauses
        public = suite.public()
        serialized = json.dumps(public)
        assert public["target"] == "/World/Lab/Microduck"
        assert public["implements"] == implements
        assert public["sceneContentHash"] == arena.content_hash
        assert len(public["episodes"]) >= 4
        test_ids = [ep["id"] for ep in suite.metadata["episodes"] if ep["split"] == "test"]
        assert test_ids
        for secret in (*test_ids, *(str(v) for v in suite.seeds.values()), "success", "when", "seeds"):
            assert secret not in serialized


def test_derive_usd_mjcf_matches_microduck_asset_and_arena_scene() -> None:
    asset_dir = BUILTIN_ROOT / "assets/robot/microduck/1"
    arena_stage = BUILTIN_ROOT / "scenes/microduck-arena/1/stage.usda"

    derived_asset = derive_usd_mjcf(asset_dir / "asset.usdc")
    derived_scene = derive_usd_mjcf(
        arena_stage,
        prim_path="/World/Lab/Microduck",
        asset_packages={"robot/microduck@1": asset_dir},
    )

    for derived in (derived_asset, derived_scene):
        assert derived.fixed_base is False
        assert derived.root_body == "trunk_base"
        assert derived.joint_names == EXPECTED_JOINTS
        assert set(derived.sites) == set(EXPECTED_SITES)
        for j, want_q0 in zip(derived.joints, EXPECTED_STAND2, strict=True):
            assert j.kind == "hinge"
            assert j.limited is True
            assert j.actuated is True
            assert j.lower < want_q0 < j.upper
            assert j.default_position == pytest.approx(want_q0, abs=1e-5)
            assert j.stiffness == pytest.approx(0.55, rel=1e-5)
            assert j.damping == pytest.approx(0.053, rel=1e-5)
            assert j.max_force == pytest.approx(0.96, rel=1e-5)
            assert j.armature == pytest.approx(0.0018, rel=1e-5)
            assert j.frictionloss == pytest.approx(0.0048, rel=1e-5)

    entity_key = "/World/Lab/Microduck"
    canonical_keys = [f"{entity_key}/joints/{name}" for name in EXPECTED_JOINTS]
    obs_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "joint_pos",
                "source": "joint_position",
                "keys": canonical_keys,
                "frame": "world",
                "unit": "rad",
                "shape": [14],
                "offset": list(EXPECTED_STAND2),
                "scale": [1.0] * 14,
            },
            {
                "name": "joint_vel",
                "source": "joint_velocity",
                "keys": canonical_keys,
                "frame": "world",
                "unit": "rad/s",
                "shape": [14],
                "offset": [0.0] * 14,
                "scale": [1.0] * 14,
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": canonical_keys,
            "units": ["rad"] * 14,
            "defaultPosition": list(EXPECTED_STAND2),
            "scale": [0.25] * 14,
            "lower": [j.lower for j in derived_scene.joints],
            "upper": [j.upper for j in derived_scene.joints],
        },
    }
    verify_against_observation_spec(
        derived_asset,
        obs_spec,
        entity_key=entity_key,
        expected_kp=[0.55] * 14,
        expected_kd=[0.053] * 14,
        check_observation_offsets=True,
    )
    verify_against_observation_spec(
        derived_scene,
        obs_spec,
        expected_kp=[0.55] * 14,
        expected_kd=[0.053] * 14,
        check_observation_offsets=True,
    )


def test_microduck_mjmodel_masses_and_zero_phantom_self_collisions(tmp_path: Path) -> None:
    mujoco = pytest.importorskip("mujoco")

    asset_dir = BUILTIN_ROOT / "assets/robot/microduck/1"
    arena_stage = BUILTIN_ROOT / "scenes/microduck-arena/1/stage.usda"
    derived_asset = derive_usd_mjcf(asset_dir / "asset.usdc")
    derived_scene = derive_usd_mjcf(
        arena_stage,
        prim_path="/World/Lab/Microduck",
        asset_packages={"robot/microduck@1": asset_dir},
    )

    for derived in (derived_asset, derived_scene):
        model = derived.to_mjmodel()
        assert model.nbody == 16  # world + 15 rigid bodies
        assert model.nq == 21  # 7 freejoint + 14 hinges
        assert model.nv == 20  # 6 freejoint + 14 hinges
        assert model.nu == 14
        total_mass = float(model.body_mass[1:].sum())
        assert total_mass == pytest.approx(sum(EXPECTED_BODY_MASSES.values()), rel=1e-6)
        for body_name, want_mass in EXPECTED_BODY_MASSES.items():
            body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
            assert body_id > 0
            assert float(model.body_mass[body_id]) == pytest.approx(want_mass, rel=1e-6)
            assert all(val > 0.0 for val in model.body_inertia[body_id])

    assets = AssetLibrary(tmp_path / "state", builtin_root=BUILTIN_ROOT, user_root=tmp_path / "user")
    assets.startup()
    cfg = assets.resolve("robot/microduck@1").metadata["implements"][0]["config"]
    bow_pose = np.asarray(cfg["actionTargets"]["bow"], dtype=np.float64)

    model = derived_asset.to_mjmodel()
    data = mujoco.MjData(model)
    for label, q_joints in (("STAND2", np.asarray(EXPECTED_STAND2, dtype=np.float64)), ("bow", bow_pose)):
        data.qpos[:3] = derived_asset.root_pos
        data.qpos[3:7] = derived_asset.root_quat
        data.qpos[7:] = q_joints
        mujoco.mj_forward(model, data)
        assert data.ncon == 0, f"Expected 0 self-contacts at {label}, got {data.ncon}"

    synth_geom_ids = set()
    for body_name in SYNTHETIC_INTERMEDIATE_BODIES:
        geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"{body_name}_collision_0")
        assert geom_id >= 0, f"Missing synthetic collider on {body_name}"
        synth_geom_ids.add(geom_id)

    lo = np.asarray([j.lower for j in derived_asset.joints], dtype=np.float64)
    hi = np.asarray([j.upper for j in derived_asset.joints], dtype=np.float64)
    rng = np.random.default_rng(42)
    offending: list[tuple[int, str, str, float]] = []
    for sample_idx in range(500):
        data.qpos[:7] = [0.0, 0.0, 0.25, 1.0, 0.0, 0.0, 0.0]
        data.qpos[7:] = rng.uniform(lo, hi)
        mujoco.mj_forward(model, data)
        for con_idx in range(data.ncon):
            con = data.contact[con_idx]
            if con.geom1 in synth_geom_ids or con.geom2 in synth_geom_ids:
                b1 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, model.geom_bodyid[con.geom1])
                b2 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, model.geom_bodyid[con.geom2])
                offending.append((sample_idx, b1, b2, float(con.dist)))
    assert offending == [], f"Synthetic P4 colliders caused phantom self-contacts: {offending[:10]}"


def test_platform_core_remains_zero_microduck_hardcoded() -> None:
    core_roots = (
        ROOT / "packages/sim_runtime",
        ROOT / "packages/policies",
        ROOT / "packages/programs",
        ROOT / "packages/training",
        ROOT / "packages/info",
        ROOT / "packages/library",
        ROOT / "packages/protocol",
        ROOT / "apps/node",
        ROOT / "apps/api",
        ROOT / "apps/web/src",
        ROOT / "apps/cli",
        ROOT / "devices/types",
    )
    offenders: list[str] = []
    for folder in core_roots:
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "microduck" in text.lower():
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], f"Platform core must not contain robot-specific literals: {offenders}"
