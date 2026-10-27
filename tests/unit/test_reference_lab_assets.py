"""The contributed scene is offline visual content, with bounded immutable packages."""

import hashlib
import json
from pathlib import Path

import numpy as np
import trimesh
from pxr import Usd, UsdGeom, UsdPhysics

from packages.library.assets import AssetLibrary
from packages.library.metadata import asset_metadata, scene_metadata
from packages.library.scenes import SceneLibrary
from packages.library.visuals import glb_budget

ROOT = Path(__file__).resolve().parents[2]


def test_reference_source_bytes_and_runtime_parts_are_self_contained():
    source = ROOT / "assets/authoring/fr3-culture-lab/1"
    manifest = json.loads((source / "source-manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest.items():
        value = (source / name).read_bytes()
        assert len(value) == expected["bytes"]
        assert hashlib.sha256(value).hexdigest() == expected["sha256"]
    packages = sorted((ROOT / "library/assets").glob("*/fr3-culture-*/1"))
    assert len(packages) == 3
    bounds = []
    for package in packages:
        metadata = asset_metadata(package)
        assert metadata["dressing"] is True
        assert not metadata.get("implements")
        stage = Usd.Stage.Open(str(package / metadata["stage"]))
        assert stage.GetDefaultPrim().GetPath().pathString == "/Asset"
        assert UsdGeom.GetStageUpAxis(stage) == "Z"
        assert UsdGeom.GetStageMetersPerUnit(stage) == 1
        assert any(prim.IsA(UsdGeom.Mesh) for prim in stage.Traverse())
        assert not any(prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.HasAPI(UsdPhysics.CollisionAPI)
                       for prim in stage.Traverse())
        assert glb_budget(package / metadata["visual"]) <= 350000
        provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
        assert provenance["sourceSha256"] == manifest["laboratory_final.blend"]["sha256"]
        for name, expected in provenance["files"].items():
            assert hashlib.sha256((package / name).read_bytes()).hexdigest() == expected["sha256"]
        bounds.append(trimesh.load_scene(package / metadata["visual"], process=False).bounds)
    # Independent source geometry: the floor is below Z=0 and the rear wall is
    # about 2.9m high. An accidental Y-up export cannot satisfy these bounds.
    overall = np.array([np.min(np.array(bounds)[:, 0], axis=0), np.max(np.array(bounds)[:, 1], axis=0)])
    assert -.2 < overall[0, 2] < 0
    assert 2.8 < overall[1, 2] < 3.5


def test_reference_scene_is_browsable_without_blender_or_device_claims(tmp_path):
    assets = AssetLibrary(tmp_path / "state", user_root=tmp_path / "library")
    assets.startup()
    scenes = SceneLibrary(user_root=tmp_path / "library", state_root=tmp_path / "state")
    version = scenes.resolve("fr3-culture-reference@1")
    metadata, bindings, _ = scene_metadata(version.path)
    assert metadata["sources"] == ["newton"]
    assert bindings["entities"] == []
    assert version.validation["launchable"] is True
    assert not any(row["status"] == "fail" for row in version.validation["checks"])
    assert len(version.asset_dependencies()) == 3
