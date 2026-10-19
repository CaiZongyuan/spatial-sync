"""The built-in library asset is a real derivative of the pinned platform FR3."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from packages.library.assets import AssetLibrary

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "library/assets/robot/fr3/1"


def test_fr3_provenance_derivatives_license_and_visual_are_real(tmp_path):
    provenance = json.loads((PACKAGE / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["revision"] == "f8fb7abcbeba2318814a74f3eeb02780ad7925d6"
    for relative, expected in provenance["files"].items():
        value = (PACKAGE / relative).read_bytes()
        assert len(value) == expected["bytes"]
        assert hashlib.sha256(value).hexdigest() == expected["sha256"]
    for relative, expected in provenance["sourceFiles"].items():
        value = (ROOT / "assets/robots" / relative).read_bytes()
        assert hashlib.sha256(value).hexdigest() == expected["sha256"]
    producer = provenance["producer"]
    assert hashlib.sha256((ROOT / producer["path"]).read_bytes()).hexdigest() == producer["sha256"]
    assert (PACKAGE / "LICENSE").read_bytes() == (
        ROOT / "assets/robots/franka_emika_panda/LICENSE"
    ).read_bytes()
    scene = trimesh.load_scene(PACKAGE / "visual.glb")
    assert len(scene.triangles) == 240624
    assert np.isfinite(scene.bounds).all()
    library = AssetLibrary(tmp_path / "state", user_root=tmp_path / "user")
    library.startup()
    detail = library.describe("robot/fr3@1")
    assert detail["metadata"]["dimensionsM"] == pytest.approx(np.ptp(scene.bounds, axis=0), abs=1e-6)
    assert "robot-arm/v1" in detail["types"]
    assert "license" not in detail["gaps"] and "provenance" not in detail["gaps"]
    assert "physics_validation" in detail["gaps"]
    assert {check["id"] for check in detail["validation"]["checks"] if check["status"] == "pass"} == {"S1"}
    assert (PACKAGE / "asset.usdc").read_bytes().startswith(b"PXR-USDC")
