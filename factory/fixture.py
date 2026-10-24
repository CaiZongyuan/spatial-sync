"""Self-authored SimReady-shaped source used by GPU-free factory checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from factory.simready import Candidate

LICENSE = (
    "CC0-1.0 synthetic fixture. Geometry, materials and metadata in this package "
    "were authored for spatial-sync tests and contain no third-party asset.\n"
)

ROOT = """#usda 1.0
(defaultPrim = "RootNode"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "RootNode" (
    variants = {
        string MuJoCo = "Disabled"
        string Newton = "Disabled"
        string PhysX = "Enabled"
    }
    prepend variantSets = ["MuJoCo", "Newton", "PhysX"]
    payload = @./payloads/base.usda@
) {
    variantSet "MuJoCo" = {
        "Disabled" {}
        "Enabled" {
            over "Geometry" (prepend references = @./payloads/instances_mujoco.usda@</MuJoCoBody>) {}
        }
    }
    variantSet "Newton" = {
        "Disabled" {}
        "Enabled" {
            over "Geometry" (prepend references = @./payloads/instances_newton.usda@</NewtonBody>) {}
        }
    }
    variantSet "PhysX" = {
        "Disabled" {}
        "Enabled" {
            over "Geometry" (prepend references = @./payloads/instances_physx.usda@</PhysXBody>) {}
        }
    }
}
"""

BASE = """#usda 1.0
(defaultPrim = "RootNode"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "RootNode" {
    def Scope "Geometry" {
    }
}
"""

GEOMETRY = """#usda 1.0
(defaultPrim = "Box"
 metersPerUnit = 1
 upAxis = "Z")
def Mesh "Box" (
    prepend apiSchemas = ["PhysicsCollisionAPI"]
) {
    point3f[] points = [
        (-0.1, -0.1, 0), (0.1, -0.1, 0), (0.1, 0.1, 0), (-0.1, 0.1, 0),
        (-0.1, -0.1, 0.2), (0.1, -0.1, 0.2), (0.1, 0.1, 0.2), (-0.1, 0.1, 0.2)
    ]
    int[] faceVertexCounts = [4, 4, 4, 4, 4, 4]
    int[] faceVertexIndices = [
        0, 1, 2, 3, 4, 7, 6, 5, 0, 4, 5, 1,
        1, 5, 6, 2, 2, 6, 7, 3, 3, 7, 4, 0
    ]
    uniform token subdivisionScheme = "none"
    float2[] primvars:st = [
        (0, 0), (1, 0), (1, 1), (0, 1),
        (0, 0), (1, 0), (1, 1), (0, 1)
    ] (
        interpolation = "vertex"
    )
}
"""

MATERIALS = """#usda 1.0
(defaultPrim = "Materials"
 metersPerUnit = 1
 upAxis = "Z")
def Scope "Materials" {
    def Material "Fixture" {
        token outputs:surface.connect = </Materials/Fixture/Surface.outputs:surface>
        def Shader "Surface" {
            token info:id = "UsdPreviewSurface"
            color3f inputs:diffuseColor = (0.75, 0.4, 0.2)
            float inputs:metallic = 0
            float inputs:roughness = 0.7
            float inputs:opacity = 1
            token outputs:surface
        }
    }
}
"""

BODY = """#usda 1.0
(defaultPrim = "{name}"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "{name}" (
    prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]
) {{
    float physics:mass = {mass}
    float3 physics:diagonalInertia = (0.0333, 0.0333, 0.0333)
    point3f physics:centerOfMass = (0, 0, 0.1)
    def Mesh "Box" (
        prepend apiSchemas = ["MaterialBindingAPI"]
        prepend references = @./geometries.usd@</Box>
    ) {{
        prepend rel material:binding = </{name}/VisualMaterials/Fixture>
    }}
    def Scope "VisualMaterials" {{
        def Material "Fixture" (
            prepend references = @./materials.usda@</Materials/Fixture>
        ) {{
        }}
    }}
}}
"""


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def build_source(root):
    """Create a complete synthetic source package and return its pinned identity."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    simready = root / "simready_usd"
    _write(simready / "sm_fixture_box.usd", ROOT)
    _write(simready / "payloads/base.usda", BASE)
    _write(simready / "payloads/geometries.usd", GEOMETRY)
    _write(simready / "payloads/materials.usda", MATERIALS)
    _write(simready / "payloads/instances_newton.usda", BODY.format(name="NewtonBody", mass=20))
    _write(simready / "payloads/instances_mujoco.usda", BODY.format(name="MuJoCoBody", mass=3))
    _write(simready / "payloads/instances_physx.usda", BODY.format(name="PhysXBody", mass=9))
    _write(root / ".metadata/LICENSE.txt", LICENSE)
    _write(
        root / ".metadata/com.nvidia.simready.root_usds.json",
        json.dumps({"format_version": "1.0", "entries": ["simready_usd/sm_fixture_box.usd"]}, indent=2)
        + "\n",
    )
    _write(
        root / "com.nvidia.simready.packaging.json",
        json.dumps(
            {
                "format_version": "1.0",
                "package_id": "fixture.synthetic.box",
                "license": "CC0-1.0",
                "metadata": [],
            },
            indent=2,
        )
        + "\n",
    )
    items = []
    for path in sorted(simready.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            value = path.read_bytes()
            items.append(
                {
                    "relative_path": relative,
                    "size": len(value),
                    "hash": {"sha256": hashlib.sha256(value).hexdigest()},
                }
            )
    _write(
        root / ".metadata/com.nvidia.simready.packaging.bom.json",
        json.dumps({"format_version": "1.0", "items": items}, indent=2) + "\n",
    )
    files = [path for path in root.rglob("*") if path.is_file()]
    candidate = Candidate(
        package="fixture_synthetic_box",
        asset_id="environment/fixture-box",
        title="Synthetic fixture box",
        description="Self-authored factory fixture.",
        expected_files=len(files),
        expected_bytes=sum(path.stat().st_size for path in files),
        role="factory CI fixture",
    )
    return candidate, root
