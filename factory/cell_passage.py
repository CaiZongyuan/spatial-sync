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
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt

LAB = "/World/Lab"
WELLS = {
    f"{row}{col}": ((col - 2) * 0.039, y, 0.002)
    for row, y in (("A", -0.0195), ("B", 0.0195))
    for col in range(1, 4)
}
HANDLE = (0.070, 0, 0.075)
CONTACT_GAP_M = 0.002
TIP_MATING_Z_M = 0.050
TIP_HEIGHT_M = 0.062
TIP_BORE_BOTTOM_RADIUS_M = 0.0007
TIP_BORE_TOP_RADIUS_M = 0.00515
TIP_SOCKET_INNER_RADIUS_M = 0.0054


def author_contact_gap(stage):
    """Bound inactive candidates for millimetre fixtures, preserving their surfaces."""
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            prim.CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(CONTACT_GAP_M)


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
    shape.GetPrim().CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(CONTACT_GAP_M)
    return shape


def cylinder(stage, path, center, radius, height, color=(0.6, 0.8, 0.86)):
    shape = UsdGeom.Cylinder.Define(stage, path)
    shape.CreateRadiusAttr(radius)
    shape.CreateHeightAttr(height)
    shape.CreateAxisAttr("Z")
    translate(shape, center)
    shape.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    shape.GetPrim().CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(CONTACT_GAP_M)
    return shape


def rotate_box_z(shape, degrees):
    """Keep dimensions in the box's local frame before rotating into its parent."""
    translate_op, scale_op = shape.GetOrderedXformOps()
    rotate_op = shape.AddRotateZOp()
    rotate_op.Set(degrees)
    shape.SetXformOpOrder([translate_op, rotate_op, scale_op])


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
        rotate_box_z(shape, math.degrees(angle))


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
        box(stage, path + "/Cover", (-0.0025, 0, 0.002), (0.125, 0.088, 0.004), (0.72, 0.8, 0.82))
        for sign in (-1, 1):
            box(stage, path + f"/CoverNotch{sign + 1}", (0.0625, sign * 0.027, 0.002), (0.005, 0.034, 0.004))
        for sign in (-1, 1):
            box(stage, path + f"/SkirtY{sign + 1}", (0, sign * 0.0445, -0.004), (0.133, 0.0015, 0.016))
        box(stage, path + "/SkirtLeft", (-0.0665, 0, -0.004), (0.0015, 0.088, 0.016))
        # The opposite carrier's raised arm passes through this explicit notch.
        for sign in (-1, 1):
            box(
                stage, path + f"/SkirtRight{sign + 1}", (0.0665, sign * 0.028, -0.004), (0.0015, 0.032, 0.016)
            )
    else:
        floor = box(stage, path + "/Floor", (0, 0, 0.001), (0.12776, 0.08548, 0.002))
        floor.CreateDisplayOpacityAttr([0.35])
        for sign in (-1, 1):
            box(stage, path + f"/FrameX{sign + 1}", (sign * 0.06313, 0, 0.010), (0.0015, 0.08548, 0.016))
            box(stage, path + f"/FrameY{sign + 1}", (0, sign * 0.04199, 0.010), (0.12776, 0.0015, 0.016))
        for name, (x, y, z) in WELLS.items():
            ring(stage, path + "/Well" + name, (x, y, 0.010), 0.0175, 0.016)
            site(stage, path + "/" + name, (x, y, z))
    # The physical interface extends beyond the footprint; it does not occupy a
    # well or move the actual hand TCP. The post is part of this rigid carrier.
    sign = -1 if lid else 1
    box(stage, path + "/CarrierArm", (sign * 0.059, 0, 0.006), (0.038, 0.018, 0.012), (0.2, 0.3, 0.4))
    cylinder(stage, path + "/Handle", (sign * HANDLE[0], 0, 0.048), 0.0085, 0.080, (0.2, 0.3, 0.4))
    site(stage, path + "/Grasp", (sign * HANDLE[0], 0, HANDLE[2]))
    return root


def tube(stage, path, *, lid=False):
    root = rigid(stage, path, 0.004)
    if lid:
        cylinder(stage, path + "/Cap", (0, 0, 0.004), 0.0105, 0.008)
        ring(stage, path + "/Skirt", (0, 0, -0.004), 0.0095, 0.008, 0.001, 20)
        box(stage, path + "/CarrierArm", (-0.0225, 0, 0.009), (0.025, 0.006, 0.002))
        ring(stage, path + "/Handle", (-0.035, 0, 0.022), 0.00835, 0.024, 0.0003, 16)
        site(stage, path + "/Grasp", (-0.035, 0, 0.022))
    else:
        cylinder(stage, path + "/Floor", (0, 0, 0.001), 0.0085, 0.002)
        ring(stage, path + "/Shell", (0, 0, 0.026), 0.008, 0.048, 0.001, 20)
        site(stage, path + "/Content", (0, 0, 0.003))
        box(stage, path + "/CarrierArm", (0.02125, 0, 0.029), (0.0275, 0.006, 0.002))
        ring(stage, path + "/Handle", (0.035, 0, 0.038), 0.00835, 0.024, 0.0003, 16)
        box(stage, path + "/RackIndex", (-0.015, 0, 0.020), (0.014, 0.006, 0.004))
        site(stage, path + "/Grasp", (0.035, 0, 0.038))
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


def tip(stage, path):
    root = rigid(stage, path, 0.001)
    # Twelve convex wall sectors leave a real tapered bore (>1 mL below the
    # 10 mm source nozzle) rather than substituting a filled cone.
    # These triangles already have consistent outward winding. Author the
    # fixed topology directly, without optional graph/normal-repair packages.
    faces = [
        [0, 1, 3],
        [0, 3, 2],
        [4, 6, 7],
        [4, 7, 5],
        [0, 4, 5],
        [0, 5, 1],
        [2, 3, 7],
        [2, 7, 6],
        [0, 2, 6],
        [0, 6, 4],
        [1, 5, 7],
        [1, 7, 3],
    ]
    for index in range(12):
        points = []
        angle = index * math.pi / 6
        for z, inner, outer in (
            (0, TIP_BORE_BOTTOM_RADIUS_M, 0.0011),
            (TIP_MATING_Z_M, TIP_BORE_TOP_RADIUS_M, 0.0057),
        ):
            for radius in (inner, outer):
                for theta in (angle, angle + math.pi / 6):
                    points.append((radius * math.cos(theta), radius * math.sin(theta), z))
        shape = UsdGeom.Mesh.Define(stage, path + f"/Nozzle/Wall{index:02d}")
        shape.CreatePointsAttr(Vt.Vec3fArray(points))
        shape.CreateFaceVertexCountsAttr([3] * len(faces))
        shape.CreateFaceVertexIndicesAttr([index for face in faces for index in face])
        shape.CreateSubdivisionSchemeAttr("none")
        shape.CreateDisplayColorAttr([Gf.Vec3f(0.6, 0.8, 0.86)])
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(shape.GetPrim()).CreateApproximationAttr("convexHull")
        shape.GetPrim().CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(CONTACT_GAP_M)
    ring(
        stage,
        path + "/Collar",
        (0, 0, (TIP_MATING_Z_M + TIP_HEIGHT_M) / 2),
        TIP_SOCKET_INNER_RADIUS_M + 0.0003,
        TIP_HEIGHT_M - TIP_MATING_Z_M,
        0.0006,
        16,
    )
    site(stage, path + "/Content", (0, 0, 0))
    return root


def package(output, name, author, *, kind="labware"):
    target = output / "assets" / kind / name / "1"
    target.mkdir(parents=True)
    stage = Usd.Stage.CreateNew(str(target / "asset.usda"))
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    author(stage, "/Asset")
    stage.SetDefaultPrim(stage.GetPrimAtPath("/Asset"))
    stage.GetRootLayer().Save()
    visual = None
    if stage.GetPrimAtPath("/Asset").HasAPI(UsdPhysics.RigidBodyAPI):
        from factory.cell_passage_visuals import export

        visual = "visual.glb"
        (target / visual).write_bytes(export(stage))
    cache = UsdGeom.BBoxCache(0, ["default", "guide"])
    dimensions = list(cache.ComputeWorldBound(stage.GetPrimAtPath("/Asset")).ComputeAlignedRange().GetSize())
    (target / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": kind + "/" + name,
                "kind": kind,
                "title": name.replace("-", " "),
                "stage": "asset.usda",
                **({"visual": visual} if visual else {}),
                "dimensionsM": dimensions,
                "physics": {
                    "massKg": sum(
                        UsdPhysics.MassAPI(p).GetMassAttr().Get() or 0
                        for p in stage.Traverse()
                        if p.HasAPI(UsdPhysics.MassAPI)
                    ),
                    "massSource": "estimated",
                    "collision": "authored",
                },
                "dressing": False,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (target / "LICENSE.txt").write_text("CC0-1.0\nSelf-authored procedural geometry.\n", encoding="utf-8")
    from factory.cell_passage_provenance import file_hashes

    (target / "provenance.json").write_text(
        json.dumps(
            {
                "generator": "factory/cell_passage.py",
                "physicsVerified": False,
                "scope": "Physical hollow labware and carrier; no modeled material state.",
                "files": file_hashes(target),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def build_labware(output):
    from factory.cell_passage_tip import ASSET_NAME, PROFILE, PROTOTYPE_SHA256, relief
    from packages.library.common import content_hash

    output = Path(output)
    packages = [
        package(output, name, author)
        for name, author in (
            ("passage-six-well-carrier", plate),
            ("passage-plate-lid", lambda s, p: plate(s, p, lid=True)),
            ("passage-collection-tube", tube),
            ("passage-tube-cap", lambda s, p: tube(s, p, lid=True)),
            ("passage-reservoir", vessel),
            ("passage-waste", lambda s, p: vessel(s, p, (0.12, 0.10, 0.035))),
            ("passage-tip-waste", lambda s, p: vessel(s, p, (0.20, 0.20, 0.107), mass=0.25)),
            ("passage-tip-1000", tip),
        )
    ]
    variant = package(output, ASSET_NAME, relief)
    provenance_path = variant / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["derivedFrom"] = {
        "asset": "labware/passage-tip-1000@1",
        "importContentHash": content_hash(output / "assets/labware/passage-tip-1000/1"),
        "prototypeSha256": PROTOTYPE_SHA256,
        "profileZInnerVertexRadiusOuterVertexRadiusM": PROFILE,
        "scope": "Self-authored geometry variant; original nozzle, TCP and tip Root are unchanged",
    }
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return [*packages, variant]


def build(source, output, *, bindings_overlay=None, with_workcell=False, demonstration=False):
    from packages.library.common import content_hash

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
        receipts.append(
            {"id": metadata["id"] + "@1", "source": original.name, "contentHash": content_hash(original)}
        )
    build_labware(output)
    from factory.cell_passage_fixtures import bench, inverted_imager, tube_rack, wide_tube_rack
    from factory.cell_passage_incubator import build as build_incubator
    from factory.cell_passage_layout import centrifuge, populate

    build_incubator(source, output)
    package(output, "passage-centrifuge", lambda s, p: centrifuge(s, p, (0, 0, 0)), kind="device")
    package(output, "passage-open-imager", inverted_imager, kind="device")
    package(output, "passage-tube-rack", tube_rack)
    if demonstration:
        package(output, "passage-tube-rack-wide", wide_tube_rack)
    package(output, "passage-preparation-bench", bench, kind="environment")
    package(output, "passage-back-bench", lambda s, p: bench(s, p, 2.4, 0.9, 0.82), kind="environment")
    scene = output / "scenes/cell-passage/1"
    scene.mkdir(parents=True)
    shutil.copyfile(source_stage, scene / "stage.usda")
    stage = Usd.Stage.Open(str(scene / "stage.usda"))
    layout = populate(stage, demonstration=demonstration)
    if demonstration:
        layout["demonstration"] = {"enabled": True, "contactAllowanceM": .002,
                                   "alignmentToleranceM": .001, "preInsertionHeightM": .011,
                                   "formalPrecisionQualified": False}
        layout["demonstration"]["rackSeatingContactAllowanceM"] = .005
        layout["demonstration"]["rackSeatingContactPairs"] = [
            [LAB + "/Labware/" + name + "/Floor",
             LAB + "/Labware/CollectionRack/Frame/" + name + "Floor"]
            for name in [f"S{i}" for i in range(1, 7)] + ["B5", "B6"]
        ]
        layout["pipetteCalibration"]["alignmentToleranceM"] = .001
    from factory.cell_passage_tip import ASSET_NAME

    for name, asset in (("tipGeometry", ASSET_NAME), ("solidWasteRegion", "passage-tip-waste")):
        path = output / "assets/labware" / asset / "1"
        authored = Usd.Stage.Open(str(path / "asset.usda"))
        bounds = (
            UsdGeom.BBoxCache(0, ["default", "guide"])
            .ComputeWorldBound(authored.GetPrimAtPath("/Asset"))
            .ComputeAlignedRange()
        )
        lower, upper = list(bounds.GetMin()), list(bounds.GetMax())
        if name == "solidWasteRegion":
            lower = [lower[0] + 0.002, lower[1] + 0.002, lower[2] + 0.002]
            upper = [upper[0] - 0.002, upper[1] - 0.002, upper[2]]
        layout[name] = {
            "asset": "labware/" + asset + "@1",
            "contentHash": content_hash(path),
            "lowerLocal": lower,
            "upperLocal": upper,
        }
    layout["solidWasteRegion"].update(bodyKey=layout["bodies"]["SolidWaste"], contactAllowanceM=0.001)
    layout["tipGeometry"].update(
        tipMatingOffsetLocalM=[0, 0, TIP_MATING_Z_M],
        tipAxisLocal=[0, 0, 1],
        headMatingOffsetLocalM=[0, 0, TIP_MATING_Z_M],
        headAxisLocal=[0, 0, 1],
        socketDepthM=TIP_HEIGHT_M - TIP_MATING_Z_M,
        socketInnerDiameterM=2 * TIP_SOCKET_INNER_RADIUS_M,
        nozzleOuterDiameterM=0.010,
    )
    from factory.cell_passage_bindings import bindings

    mechanical = bindings(stage, layout)
    author_contact_gap(stage)
    if with_workcell:
        from packages.programs.cell_passage.scene import workcell_binding

        if bindings_overlay:
            raise ValueError("Select either the workcell generator or an external binding overlay")
        workcell = workcell_binding(layout)
        workcell["config"].update(layout["motionLimits"])
        mechanical["entities"].append(workcell)
    if bindings_overlay:
        extra = yaml.safe_load(Path(bindings_overlay).read_text(encoding="utf-8"))
        extra = extra.get("entities", [extra])
        owned = {row["key"] for row in mechanical["entities"]}
        if any(row["key"] in owned for row in extra):
            raise ValueError("Overlay cannot replace a mechanical owner")
        mechanical["entities"].extend(extra)
    if demonstration:
        for binding in mechanical["entities"]:
            if binding["program"] in ("labware.motion", "cell_passage.joint", "cell_passage.workcell"):
                binding["config"]["contactAllowanceM"] = .002
            if (binding["program"] == "labware.motion"
                    or binding["key"] == LAB + "/RailCarriage"):
                binding["config"]["seatingContactPairs"] = layout["demonstration"][
                    "rackSeatingContactPairs"
                ]
                binding["config"]["seatingContactAllowanceM"] = layout["demonstration"][
                    "rackSeatingContactAllowanceM"
                ]
            if binding["program"] == "labware.motion":
                binding["config"]["jointAccelerationRadPerS2"] = .5
                binding["config"]["symmetricPreInsertionLocations"] = list(
                    layout["incubatorTransitLocations"]
                )
            if binding["program"] == "cell_passage.workcell":
                binding["config"]["axisToleranceM"] = .001
    stage.GetRootLayer().Save()
    (scene / "mechanical-layout.json").write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    for name, content in {
        "scene.yaml": {
            "name": "cell-passage",
            "title": "Cell Passage workcell",
            "description": ("Approximate demonstration: 2 mm contacts, with 5 mm only for eight exact "
                            "tube-floor/rack-floor seating pairs; not formal precision qualification."
                            if demonstration else "Physical workcell; qualification receipts are separate."),
            "sources": ["newton"],
            "stage": "stage.usda",
            "simulation": {"solver": "mujoco", "warmupSteps": 1},
        },
        "devices.yaml": mechanical,
        "locations.yaml": {"locations": {}},
    }.items():
        (scene / name).write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    shutil.copyfile(source / "scene/provenance.json", scene / "SOURCE-PROVENANCE.json")
    (scene / "provenance.json").write_text(
        json.dumps(
            {
                "schema": "lw.cell-passage-scene-provenance/1",
                "sourceStageSha256": hashlib.sha256(source_stage.read_bytes()).hexdigest(),
                "upstream": "SOURCE-PROVENANCE.json",
                "sourceDependencies": receipts,
                "changes": [
                    "Single-level simulation incubator derivative, synchronized collision and GLB",
                    "Original FR3 link geometry, hand frame, TCP and rail dimensions retained",
                    "Repositioned incubator and Flex; Flex Z restricted to 80 mm",
                    "Physical carriers, lids, tips, reservoirs, fixtures and four-slot centrifuge",
                ],
                "mechanicallyQualified": False,
                "contactGapM": CONTACT_GAP_M,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "source-manifest.json").write_text(
        json.dumps(
            {
                "schema": "lw.cell-passage-factory/1",
                "sourceStageSha256": hashlib.sha256(source_stage.read_bytes()).hexdigest(),
                "dependencies": receipts,
                "physicsVerified": False,
                "generatorFiles": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(Path(__file__).parent.glob("cell_passage*.py"))
                },
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
    parser.add_argument("--bindings-overlay", type=Path)
    parser.add_argument("--demonstration", action="store_true", help="Explicit approximate demonstration")
    parser.add_argument(
        "--with-workcell", action="store_true", help="Assemble the P2-owned workcell generator output"
    )
    args = parser.parse_args()
    print(
        build(args.source, args.out, bindings_overlay=args.bindings_overlay, with_workcell=args.with_workcell,
              demonstration=args.demonstration)
    )
