"""Build and import a local static reference from a World Building laboratory.

Blender and original mixed-source models stay outside the gateway and Git.
Imports use the real lw CLI and its existing HTTP pipeline.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import numpy as np
import trimesh
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory.visual_geometry import surface_signature
from factory.worldbuilding_blender import RECIPE
from packages.library.visuals import glb_budget

SCENE = "panda-cell-passaging-reference"
LOCAL_AUTHORIZATION = """World Building local user contribution and use authorization

The user supplied the completed cell-passaging-lab project and explicitly asked
on 2026-11-07 to import its scene and materials into CaiZongyuan/spatial-sync.
This record permits retaining this derivative in that user's local library for
the requested platform experiment and its rendered tutorial/video evidence.
Source: spatial-sync issue #252 and its visual-intake sub-issue #253.

Raw Blender files, meshes and derived Asset binaries remain private/local.
No general-purpose open-source license, commercial-use qualification or public
raw-asset redistribution right is asserted. This is authorization for the local
user contribution, not a warranty that every supplied file has cleared all
external rights. Actual third-party license terms and attribution remain in
their original files; unresolved source/distribution questions stay qualified.
"""


def write_json(path, document):
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fingerprint(path):
    value = path.read_bytes()
    return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}


def safe_relative(value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or PureWindowsPath(value).drive or PurePosixPath(value).is_absolute()
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("Expected a bounded relative package path")
    return Path(value)


def owned_tree(root):
    """Reject redirected inputs/outputs before any package metadata is written."""
    root = root.absolute()
    if any(path.is_symlink() or path.is_junction() for path in (root, *root.parents)):
        raise ValueError("Owned output paths must not contain symlinks or junctions")
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Owned output paths must not contain symlinks or junctions")
            if path.is_dir():
                pending.append(path)


def preflight_packages(project, derived, receipt):
    owned_tree(derived)
    if receipt["source"] != "07-render/render_scene_lookdev.blend":
        raise ValueError("Source must be the project's final lookdev file")
    source = project / safe_relative(receipt["source"])
    if source.is_symlink() or not source.resolve().is_relative_to(project.resolve()):
        raise ValueError("Source dependency escapes the project")
    names = set()
    for chunk in receipt["chunks"]:
        name = chunk["name"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-[1-9][0-9]*", name) or name in names:
            raise ValueError("Chunk names must be unique bounded identifiers")
        names.add(name)
        directory = derived / name
        if not directory.is_dir() or not directory.resolve().is_relative_to(derived.resolve()):
            raise ValueError("Chunk directory escapes the derivative")
        if chunk["kind"] not in ("robot", "device", "labware", "environment"):
            raise ValueError("Unknown local asset kind")
        for filename, expected in chunk["files"].items():
            path = directory / safe_relative(filename)
            if not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Derived dependency escapes its package")
            if fingerprint(path) != expected:
                raise ValueError("Derived bytes changed: " + filename)


def decoded_visual(path):
    """Read actual GLB position bytes and node transforms, independently of Blender."""
    data = path.read_bytes()
    json_size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + json_size])
    binary_start = 20 + json_size + 8
    nodes, result = document.get("nodes", []), {}

    def positions(index):
        accessor = document["accessors"][index]
        if accessor["componentType"] != 5126 or accessor["type"] != "VEC3" or "sparse" in accessor:
            raise ValueError("Intake visuals require embedded float32 VEC3 positions")
        view = document["bufferViews"][accessor["bufferView"]]
        offset = binary_start + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        return np.ndarray((accessor["count"], 3), dtype="<f4", buffer=data, offset=offset,
                          strides=(view.get("byteStride", 12), 4)).astype(float)

    def indices(primitive, count):
        if "indices" not in primitive:
            return np.arange(count)
        accessor = document["accessors"][primitive["indices"]]
        view = document["bufferViews"][accessor["bufferView"]]
        dtype = {5121: "u1", 5123: "<u2", 5125: "<u4"}[accessor["componentType"]]
        offset = binary_start + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        result = np.frombuffer(data, dtype=dtype, count=accessor["count"], offset=offset)
        if np.any(result >= count):
            raise ValueError("GLB triangle index exceeds its actual positions")
        return result

    def visit(index, parent):
        node = nodes[index]
        if "matrix" in node:
            local = np.array(node["matrix"]).reshape(4, 4).T
        else:
            q = node.get("rotation", [0, 0, 0, 1])
            local = trimesh.transformations.quaternion_matrix([q[3], *q[:3]])
            local[:3, :3] @= np.diag(node.get("scale", [1, 1, 1]))
            local[:3, 3] = node.get("translation", [0, 0, 0])
        world = parent @ local
        if "mesh" in node:
            primitives = document["meshes"][node["mesh"]]["primitives"]
            points, all_indices, triangles, offset = [], [], 0, 0
            for primitive in primitives:
                if primitive.get("mode", 4) != 4:
                    raise ValueError("Intake requires ordinary GLB triangles")
                vertices = positions(primitive["attributes"]["POSITION"])
                face_indices = indices(primitive, len(vertices))
                triangles += len(face_indices) // 3
                all_indices.append(face_indices + offset)
                offset += len(vertices)
                points.append(vertices)
            geometry, surface_points = surface_signature(
                np.concatenate(points), [3] * triangles, np.concatenate(all_indices))
            transformed = trimesh.transform_points(surface_points, world)
            result[node["name"]] = {"matrix": world, "points": transformed, "triangles": triangles,
                                    "nonDegenerateTriangles": geometry["faceCounts"]["3"],
                                    "geometry": geometry}
        for child in node.get("children", []):
            visit(child, world)

    for index in document["scenes"][document.get("scene", 0)]["nodes"]:
        visit(index, np.eye(4))
    return result


def audit_visual(directory, chunk):
    unique_triangles = glb_budget(directory / "visual.glb")
    decoded = decoded_visual(directory / "visual.glb")
    placement, points, max_error = np.asarray(chunk["placement"]), [], 0.0
    expected_names = {chunk["nodes"][row["name"]] for row in chunk["objects"]}
    if set(decoded) != expected_names:
        raise ValueError("GLB source-object completeness differs: " + chunk["name"])
    for row in chunk["objects"]:
        if "sourceTriangleGeometry" not in row or "sourcePolygonGeometry" not in row:
            raise ValueError("Missing independent source surface witness: " + row["name"])
        node = decoded[chunk["nodes"][row["name"]]]
        if node["nonDegenerateTriangles"] != row.get("nonDegenerateTriangles", row["triangles"]):
            raise ValueError("Decoded GLB nondegenerate triangle count differs: " + row["name"])
        if node["geometry"] != row["sourceTriangleGeometry"]:
            raise ValueError("Decoded GLB surface payload differs: " + row["name"])
        observed = placement @ node["matrix"]
        if not np.allclose(observed, row["sourceWorld"], atol=2e-6, rtol=0):
            raise ValueError("Decoded GLB object transform differs: " + row["name"])
        world_points = trimesh.transform_points(node["points"], placement)
        bounds = np.array([world_points.min(axis=0), world_points.max(axis=0)])
        error = float(np.max(np.abs(bounds - row["sourceWorldBounds"])))
        max_error = max(max_error, error)
        expected = np.asarray(row["sourceWorldBounds"])
        if not np.allclose(bounds, expected, atol=2e-5, rtol=0):
            raise ValueError("Decoded GLB source bounds differ: " + row["name"])
        points.append(node["points"])
    all_points = np.concatenate(points)
    bounds = np.array([all_points.min(axis=0), all_points.max(axis=0)])
    stage = Usd.Stage.Open(str(directory / "asset.usdc"))
    if not stage or not stage.GetPrimAtPath("/Asset"):
        raise ValueError("Derived stage must contain /Asset")
    if UsdGeom.GetStageUpAxis(stage) != "Z" or UsdGeom.GetStageMetersPerUnit(stage) != 1:
        raise ValueError("Derived stage must remain metres/Z-up")
    if any(p.HasAPI(UsdPhysics.RigidBodyAPI) or p.HasAPI(UsdPhysics.CollisionAPI) for p in stage.Traverse()):
        raise ValueError("Visual intake cannot introduce physics")
    for prim in stage.Traverse():
        for attribute in prim.GetAttributes():
            value = attribute.Get()
            if not isinstance(value, Sdf.AssetPath) or not value.path:
                continue
            dependency = value.path
            if (PurePosixPath(dependency).is_absolute() or PureWindowsPath(dependency).drive
                    or "\\" in dependency or ":" in dependency):
                raise ValueError("USD dependency must be package-relative")
            resolved = (directory / dependency).resolve()
            if not resolved.is_relative_to(directory.resolve()) or not resolved.is_file():
                raise ValueError("USD dependency escapes or is missing from its package")
    transforms, usd_points, usd_objects = UsdGeom.XformCache(Usd.TimeCode.Default()), [], set()
    source_objects = {row["name"]: row for row in chunk["objects"]}
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            owner, source_name = prim, None
            while owner and not owner.IsPseudoRoot():
                source_name = owner.GetAttribute("userProperties:source_object").Get()
                if source_name:
                    break
                owner = owner.GetParent()
            if source_name not in source_objects or source_name in usd_objects:
                raise ValueError("USD source-object completeness differs: " + chunk["name"])
            usd_objects.add(source_name)
            mesh = UsdGeom.Mesh(prim)
            geometry, mesh_points = surface_signature(mesh.GetPointsAttr().Get(),
                mesh.GetFaceVertexCountsAttr().Get(), mesh.GetFaceVertexIndicesAttr().Get())
            if geometry != source_objects[source_name]["sourcePolygonGeometry"]:
                raise ValueError("USD surface payload differs: " + source_name)
            transform = np.asarray(transforms.GetLocalToWorldTransform(prim)).T
            if not np.allclose(placement @ transform, source_objects[source_name]["sourceWorld"],
                               atol=2e-6, rtol=0):
                raise ValueError("USD source object transform differs: " + source_name)
            usd_points.append(trimesh.transform_points(mesh_points, transform))
    if usd_objects != set(source_objects):
        raise ValueError("USD source-object completeness differs: " + chunk["name"])
    usd_points = np.concatenate(usd_points)
    usd_bounds = [usd_points.min(axis=0), usd_points.max(axis=0)]
    if not np.allclose(bounds, usd_bounds, atol=2e-5, rtol=0):
        raise ValueError("Independent USD/GLB bounds differ: " + chunk["name"])
    return {"meshObjects": len(decoded), "boundsM": bounds.tolist(),
            "uniqueTriangles": unique_triangles,
            "instancedTriangles": sum(node["triangles"] for node in decoded.values()),
            "sourceTriangles": chunk["triangles"],
            "maxSourceBoundsErrorM": max_error, "dimensionsM": (bounds[1] - bounds[0]).tolist()}


def package(project, derived):
    if derived.is_symlink() or derived.is_junction():
        raise ValueError("Derivative root must not be redirected")
    receipt = json.loads((derived / "derivation.json").read_text(encoding="utf-8"))
    if receipt["recipe"] != RECIPE or receipt["frame"] != 0:
        raise ValueError("Expected the current final-lookdev frame-0 recipe")
    preflight_packages(project, derived, receipt)
    if fingerprint(project / receipt["source"]) != receipt["sourceFile"]:
        raise ValueError("The source no longer matches the derivative")
    audits, packages = {}, {}
    for chunk in receipt["chunks"]:
        directory = derived / chunk["name"]
        audits[chunk["name"]] = audit_visual(directory, chunk)
        identifier = chunk["kind"] + "/panda-cell-passaging-" + chunk["name"]
        metadata = {
            "id": identifier, "kind": chunk["kind"], "title": "Panda 细胞传代 · " + chunk["name"],
            "description": "完整 World Building 场景的第0帧视觉素材。当前仅作布景，"
                           "没有设备动作、碰撞或完整实验资格。",
            "stage": "asset.usdc", "visual": "visual.glb", "dressing": True,
            "dimensionsM": audits[chunk["name"]]["dimensionsM"],
        }
        (directory / "asset.yaml").write_text(yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
                                               encoding="utf-8")
        provenance = {key: receipt[key] for key in (
            "recipe", "blender", "blenderBuild", "frame", "coordinates", "source", "sourceFile",
            "packedImages",
            "externalLibraries", "textureConversions", "materialAdaptations", "approximations", "producer",
            "geometryWitnessProducer")}
        provenance |= {"chunk": chunk, "decodedVisualAudit": audits[chunk["name"]],
                       "packager": fingerprint(Path(__file__)),
                       "licenseStatus": "unverified mixed sources; local user-library derivative only",
                       "origins": ["world-building/cell-passaging-lab", "lumen-reuse", "lumen-audit",
                                   "mujoco_menagerie@98a70c569c4c1b6da467a20012f766ee1f8dc521"]}
        (directory / "USER_CONTRIBUTION.txt").write_text(LOCAL_AUTHORIZATION, encoding="utf-8")
        (directory / "NOTICE").write_text(
            "Local derivative of the user's mixed-source World Building laboratory.\n"
            "No public distribution or commercial license qualification is asserted.\n"
            "Original device/body names and source hashes are retained in provenance.json.\n",
            encoding="utf-8")
        if chunk["kind"] == "robot":
            upstream = project.parents[1] / "assets/third_party/menagerie_franka_emika_panda"
            for original, target in (("LICENSE", "LICENSE"), ("README.md", "PANDA_README.md"),
                                     ("UPSTREAM_COMMIT.txt", "UPSTREAM_COMMIT.txt"),
                                     ("NOTICE", "PANDA_NOTICE")):
                if (upstream / original).is_file():
                    shutil.copyfile(upstream / original, directory / target)
        else:
            (directory / "LICENSE").write_text(LOCAL_AUTHORIZATION, encoding="utf-8")
        source_notices = {
            "workstation": "lumen-audit/workstation_lumen_v02/SOURCE_NOTICE.md",
            "centrifuge": "lumen-audit/centrifuge_lumen_v03_engineering/sources_and_assumptions.md",
            "imager": "lumen-reuse/plate_imager_p0/README_zh.md",
        }
        source_notice = source_notices.get(chunk["group"])
        if chunk["group"].endswith(("plate", "lid")):
            source_notice = source_notices["imager"]
        if source_notice:
            shutil.copyfile(project.parents[1] / "assets" / source_notice, directory / "SOURCE_NOTICE.md")
            provenance["sourceNotice"] = source_notice
        provenance["files"] = {path.relative_to(directory).as_posix(): fingerprint(path)
                               for path in sorted(directory.rglob("*"))
                               if path.is_file() and path.name != "provenance.json"}
        write_json(directory / "provenance.json", provenance)
        packages[chunk["name"]] = identifier
    write_json(derived / "audit.json", audits)
    return receipt, packages


def cli(url, *arguments):
    env = os.environ | {"LW_URL": url, "PYTHONPATH": os.pathsep.join((str(ROOT / "apps/cli"), str(ROOT)))}
    result = subprocess.run([sys.executable, "-m", "lw", *arguments, "--json"], cwd=ROOT, env=env,
                            text=True, capture_output=True, encoding="utf-8", timeout=300)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def compose(directory, receipt, versions):
    if any(path.is_symlink() or path.is_junction() for path in (directory, *directory.parents)):
        raise ValueError("Scene output paths must not contain symlinks or junctions")
    directory.mkdir(exist_ok=True)
    owned_tree(directory)
    stage = Usd.Stage.CreateInMemory()
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.Xform.Define(stage, "/World/Lab")
    UsdGeom.Xform.Define(stage, "/World/Dressing")
    references = []
    for chunk in receipt["chunks"]:
        kind_name, revision = versions[chunk["name"]].rsplit("@", 1)
        prim_path = "/World/Dressing/" + chunk["name"].replace("-", "_")
        obj = UsdGeom.Xform.Define(stage, prim_path)
        obj.AddTransformOp().Set(Gf.Matrix4d(np.asarray(chunk["placement"]).T.tolist()))
        references.append((prim_path, f"library:assets/{kind_name}/{revision}/asset.usdc"))
    stage.GetRootLayer().Export(str(directory / "stage.usda"))
    layer = Sdf.Layer.FindOrOpen(str(directory / "stage.usda"))
    for path, filename in references:
        layer.GetPrimAtPath(path).referenceList.prependedItems = [Sdf.Reference(filename, "/Asset")]
    layer.Save()
    documents = {
        "scene.yaml": {"name": SCENE, "title": "Panda 细胞传代实验室 · 静态视觉参考",
                       "description": "完整场景第0帧，保留 Panda、导轨与主要设备器皿。"
                                      "本版本只验证视觉入库；设备动作与14步实验流程尚未验证。",
                       "sources": ["newton"], "stage": "stage.usda",
                       "simulation": {"solver": "xpbd", "warmupSteps": 1},
                       "derivedFrom": {"recipe": RECIPE, "source": receipt["sourceFile"], "frame": 0}},
        "devices.yaml": {"scene": SCENE, "entities": []}, "locations.yaml": {"locations": {}},
    }
    for name, value in documents.items():
        (directory / name).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
                                     encoding="utf-8")
    return directory


def install(args, receipt, packages):
    imports, versions = {}, {}
    for name in packages:
        imported = cli(args.url, "asset", "import", str(args.workspace / "derived" / name))
        imports[name] = imported
        write_json(args.workspace / "imports.json", imports)
        if any(check["status"] == "fail" for check in imported["validation"]["checks"]):
            raise RuntimeError("Asset validation failed: " + name)
        versions[name] = imported["assetVersion"]
        print("Imported " + versions[name], flush=True)
    scene = compose(args.workspace / "scene", receipt, versions)
    imported = cli(args.url, "scene", "import", str(scene))
    imports["scene"] = imported
    write_json(args.workspace / "imports.json", imports)
    if any(check["status"] == "fail" for check in imported["validation"]["checks"]):
        raise RuntimeError("Scene validation failed; inspect imports.json")
    identifier = imported["sceneVersion"]
    write_json(args.workspace / "scene-detail.json", cli(args.url, "scene", "show", identifier))
    print("Validated " + identifier, flush=True)
    if args.launch:
        session = cli(args.url, "scene", "launch", identifier, "--source", "newton")
        write_json(args.workspace / "session.json", session)
        print("Owned static Session: " + session["sessionId"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "install"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=ROOT / ".scratch/worldbuilding-intake")
    parser.add_argument("--blender")
    parser.add_argument("--url", default="http://127.0.0.1:8075")
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    args.project, args.workspace = args.project.resolve(), args.workspace.resolve()
    if args.workspace == args.project or args.workspace.is_relative_to(args.project):
        parser.error("The owned workspace must stay outside the original source project")
    args.workspace.mkdir(parents=True, exist_ok=True)
    if args.action == "build":
        if not args.blender:
            parser.error("build requires --blender")
        subprocess.run([args.blender, "--background", "--factory-startup", "--threads", "2",
                        "--python-exit-code", "1", "--python",
                        str(ROOT / "factory/worldbuilding_blender.py"), "--", "--project", str(args.project),
                        "--output", str(args.workspace / "derived")], check=True)
    receipt, packages = package(args.project, args.workspace / "derived")
    if args.action == "install":
        install(args, receipt, packages)
    else:
        print(f"Built and independently checked {len(packages)} local visual packages", flush=True)


if __name__ == "__main__":
    main()
