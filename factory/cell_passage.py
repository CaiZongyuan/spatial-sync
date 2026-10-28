"""Rebuild Cell Passage packages from the pinned, read-only assets02 delivery.

Run with USD extras: python -m factory.cell_passage --source <platform_packages>
--out <fresh-output>. Source packages are copied, never edited. Mechanical
qualification is a separate Newton execution; a build makes no pass claim.
"""

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

import yaml
from pxr import Gf, Usd, UsdGeom, UsdPhysics

LAB = "/World/Lab"
WELLS = {
    f"{row}{col}": ((col - 2) * 0.039, y, 0.002)
    for row, y in (("A", -0.0195), ("B", 0.0195))
    for col in range(1, 4)
}
HANDLE = (0.085, 0, 0.075)


def translate(prim, point):
    transform = UsdGeom.Xformable(prim)
    transform.ClearXformOpOrder()
    transform.AddTranslateOp().Set(Gf.Vec3d(*point))


def box(stage, path, center, size, color=(0.36, 0.65, 0.72)):
    shape = UsdGeom.Cube.Define(stage, path)
    shape.CreateSizeAttr(1)
    translate(shape, center)
    shape.AddScaleOp().Set(Gf.Vec3f(*size))
    shape.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    return shape


def cylinder(stage, path, center, radius, height, color=(0.6, 0.8, 0.86)):
    shape = UsdGeom.Cylinder.Define(stage, path)
    shape.CreateRadiusAttr(radius)
    shape.CreateHeightAttr(height)
    shape.CreateAxisAttr("Z")
    translate(shape, center)
    shape.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    return shape


def ring(stage, path, center, radius, height, wall=0.001, segments=32):
    """Convex wall tiles leave the entire bore free for a real pipette/tube."""
    x, y, z = center
    for i in range(segments):
        angle = 2 * math.pi * i / segments
        shape = box(
            stage,
            f"{path}/Wall{i:02d}",
            (x + radius * math.cos(angle), y + radius * math.sin(angle), z),
            (wall, 2 * radius * math.tan(math.pi / segments) + wall, height),
        )
        shape.AddRotateZOp().Set(math.degrees(angle))


def site(stage, path, point):
    value = UsdGeom.Xform.Define(stage, path)
    translate(value, point)
    return value


def rigid(stage, path, mass):
    root = UsdGeom.Xform.Define(stage, path)
    UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(mass)
    return root


def plate(stage, path, *, lid=False):
    """ANSI-footprint six-well carrier with an outboard, raised 17 mm handle."""
    root = rigid(stage, path, 0.16 if not lid else 0.065)
    if lid:
        box(stage, path + "/Cover", (0, 0, 0.002), (0.130, 0.088, 0.004), (0.72, 0.8, 0.82))
    else:
        box(stage, path + "/Floor", (0, 0, 0.001), (0.12776, 0.08548, 0.002))
        for name, (x, y, z) in WELLS.items():
            ring(stage, path + "/Well" + name, (x, y, 0.010), 0.0175, 0.016)
            site(stage, path + "/" + name, (x, y, z))
    # The physical interface extends beyond the footprint; it does not occupy a
    # well or move the actual hand TCP. The post is part of this rigid carrier.
    sign = -1 if lid else 1
    box(stage, path + "/CarrierArm", (sign * 0.066, 0, 0.006), (0.054, 0.018, 0.012), (0.2, 0.3, 0.4))
    cylinder(stage, path + "/Handle", (sign * 0.085, 0, 0.048), 0.0085, 0.080, (0.2, 0.3, 0.4))
    site(stage, path + "/Grasp", (sign * 0.085, 0, 0.075))
    return root


def tube(stage, path, *, lid=False):
    root = rigid(stage, path, 0.012 if not lid else 0.004)
    if lid:
        cylinder(stage, path + "/Cap", (0, 0, 0.004), 0.0105, 0.008)
        cylinder(stage, path + "/Handle", (0, 0, 0.023), 0.0085, 0.030)
        site(stage, path + "/Grasp", (0, 0, 0.023))
    else:
        cylinder(stage, path + "/Floor", (0, 0, 0.001), 0.0085, 0.002)
        ring(stage, path + "/Shell", (0, 0, 0.050), 0.008, 0.098, 0.001, 20)
        site(stage, path + "/Content", (0, 0, 0.003))
        site(stage, path + "/Grasp", (0, 0, 0.075))
    return root


def vessel(stage, path, size=(0.08, 0.06, 0.035), *, mass=0.08):
    root = rigid(stage, path, mass)
    x, y, z = size
    box(stage, path + "/Floor", (0, 0, 0.001), (x, y, 0.002))
    for sign in (-1, 1):
        box(stage, path + f"/X{sign + 1}", (sign * (x / 2 - 0.001), 0, z / 2), (0.002, y, z))
        box(stage, path + f"/Y{sign + 1}", (0, sign * (y / 2 - 0.001), z / 2), (x, 0.002, z))
    site(stage, path + "/Content", (0, 0, 0.003))
    return root


def package(output, name, author, dimensions):
    target = output / "assets" / "labware" / name / "1"
    target.mkdir(parents=True)
    stage = Usd.Stage.CreateNew(str(target / "asset.usda"))
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    author(stage, "/Asset")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/Asset"))
    stage.GetRootLayer().Save()
    (target / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "labware/" + name,
                "kind": "labware",
                "title": name.replace("-", " "),
                "stage": "asset.usda",
                "dimensionsM": dimensions,
                "physics": {"massKg": 0.16, "massSource": "estimated", "collision": "authored"},
                "dressing": False,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (target / "LICENSE.txt").write_text("CC0-1.0\nSelf-authored procedural geometry.\n", encoding="utf-8")
    (target / "provenance.json").write_text(
        json.dumps(
            {
                "generator": "factory/cell_passage.py",
                "physicsVerified": False,
                "scope": "Physical hollow labware and carrier; no modeled material state.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def build_labware(output):
    output = Path(output)
    return [
        package(output, name, author, dimensions)
        for name, author, dimensions in (
            ("passage-six-well-carrier", plate, [0.15738, 0.08548, 0.088]),
            ("passage-plate-lid", lambda s, p: plate(s, p, lid=True), [0.1585, 0.088, 0.088]),
            ("passage-collection-tube", tube, [0.017, 0.017, 0.099]),
            ("passage-tube-cap", lambda s, p: tube(s, p, lid=True), [0.021, 0.021, 0.038]),
            ("passage-reservoir", vessel, [0.08, 0.06, 0.035]),
            ("passage-waste", lambda s, p: vessel(s, p, (0.12, 0.10, 0.10)), [0.12, 0.10, 0.10]),
        )
    ]


def build(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Output must be fresh; immutable package bytes are never overwritten")
    source_stage = source / "scene/stage.usda"
    if not source_stage.is_file():
        raise ValueError("Expected assets02 platform_packages source")
    output.mkdir(parents=True)
    receipts = []
    for original in sorted((source / "assets").iterdir()):
        metadata = yaml.safe_load((original / "asset.yaml").read_text(encoding="utf-8"))
        target = output / "assets" / metadata["id"] / "1"
        shutil.copytree(original, target)
        receipts.append({"id": metadata["id"] + "@1", "source": original.name})
    build_labware(output)
    scene = output / "scenes/cell-passage/1"
    scene.mkdir(parents=True)
    shutil.copyfile(source_stage, scene / "stage.usda")
    for name, content in {
        "scene.yaml": {
            "name": "cell-passage",
            "title": "Cell Passage workcell",
            "description": "Physical workcell; qualification receipts are separate.",
            "sources": ["newton"],
            "stage": "stage.usda",
            "simulation": {"solver": "mujoco", "warmupSteps": 1},
        },
        "devices.yaml": {"scene": "cell-passage", "entities": []},
        "locations.yaml": {"locations": {}},
    }.items():
        (scene / name).write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    (output / "source-manifest.json").write_text(
        json.dumps(
            {
                "schema": "lw.cell-passage-factory/1",
                "sourceStageSha256": hashlib.sha256(source_stage.read_bytes()).hexdigest(),
                "dependencies": receipts,
                "physicsVerified": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(build(args.source, args.out))
