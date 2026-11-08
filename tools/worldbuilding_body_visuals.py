"""Derive local rigid-body visuals from a frozen World Building frame0 export.

No Blender, physics adaptation or Source launch. Mixed-source binary assets stay
in the user's local workspace/library; imports use the ordinary HTTP pipeline.
"""

import argparse
import hashlib
import json
import re
import shutil
import struct
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import yaml
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.worldbuilding_intake import (
    LOCAL_AUTHORIZATION,
    RECIPE,
    audit_visual,
    cli,
    fingerprint,
    owned_tree,
    preflight_packages,
    safe_relative,
    write_json,
)


def rendered_meshes(document):
    """Count actual default-scene references, including duplicated references."""
    result = []

    def visit(index, ancestors):
        if index in ancestors:
            raise ValueError("GLB default scene contains a cycle")
        node = document["nodes"][index]
        if "mesh" in node:
            result.append((node["name"], len(document["meshes"][node["mesh"]]["primitives"])))
        for child in node.get("children", []):
            visit(child, ancestors | {index})

    for index in document["scenes"][document.get("scene", 0)]["nodes"]:
        visit(index, set())
    return result


def local_glb(source, target, root_name, expected_names):
    data = source.read_bytes()
    size = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + size])
    roots = [i for i, node in enumerate(document["nodes"]) if node.get("name") == root_name]
    if len(roots) != 1 or document.get("animations"):
        raise ValueError("Expected one unanimated source body root")
    node = document["nodes"][roots[0]]
    for field in ("translation", "rotation", "scale", "matrix"):
        node.pop(field, None)
    node["matrix"] = np.eye(4).reshape(-1).tolist()
    document["scenes"] = [{"name": "Body-local source frame0 appearance", "nodes": roots}]
    document["scene"] = 0
    meshes = rendered_meshes(document)
    if Counter(name for name, _ in meshes) != Counter(expected_names):
        raise ValueError("GLB body ownership or default-scene completeness differs")
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    tail = data[20 + size:]
    # Unreachable original payload is deliberately retained, never rendered.
    target.write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(encoded) + len(tail))
                       + struct.pack("<I4s", len(encoded), b"JSON") + encoded + tail)
    return sum(count for _, count in meshes)


def namespace_snapshot(stage, renames):
    def mapped(path):
        for before, after in renames.items():
            if path.HasPrefix(Sdf.Path(before)):
                return str(path.ReplacePrefix(Sdf.Path(before), Sdf.Path(after)))
        return str(path)

    return {mapped(prim.GetPath()): {"type": prim.GetTypeName(),
        "attrs": {a.GetName(): [str(a.GetTypeName()), repr(a.Get()), [mapped(p) for p in a.GetConnections()]]
                  for a in prim.GetAttributes()},
        "rels": {r.GetName(): [mapped(p) for p in r.GetTargets()] for r in prim.GetRelationships()}}
        for prim in stage.TraverseAll()}


def local_stage(source, target, source_root):
    original = Usd.Stage.Open(str(source))
    candidates = [p for p in original.Traverse()
                  if p.GetAttribute("userProperties:source_object").Get() == source_root]
    if len(candidates) != 1:
        raise ValueError("Expected one canonical source body root")
    stage = Usd.Stage.CreateNew(str(target))
    asset = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(asset.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.Xform.Define(stage, "/Asset/Appearance").CreatePurposeAttr("render")
    for prim in original.GetPrimAtPath("/Asset").GetChildren():
        if prim.GetTypeName() == "Scope":
            Sdf.CopySpec(original.GetRootLayer(), prim.GetPath(), stage.GetRootLayer(), prim.GetPath())
    copied = Sdf.Path("/Asset/Appearance/" + candidates[0].GetName())
    Sdf.CopySpec(original.GetRootLayer(), candidates[0].GetPath(), stage.GetRootLayer(), copied)
    UsdGeom.Xformable(stage.GetPrimAtPath(copied)).ClearXformOpOrder()
    renames = {}
    for prim in stage.TraverseAll():
        if not prim.GetName().isascii():
            if "/_materials/" not in str(prim.GetPath()):
                raise ValueError("Non-ASCII source prim outside material namespace")
            name = "shader_" + hashlib.sha256(prim.GetName().encode()).hexdigest()[:12]
            renamed = prim.GetPath().GetParentPath().AppendChild(name)
            if stage.GetPrimAtPath(renamed):
                raise ValueError("Material namespace normalization collides")
            renames[str(prim.GetPath())] = str(renamed)
    before = namespace_snapshot(stage, renames)
    for a, b in renames.items():
        editor = Usd.NamespaceEditor(stage)
        if not editor.MovePrimAtPath(a, b) or not editor.CanApplyEdits() or not editor.ApplyEdits():
            raise ValueError("Cannot preserve material connections while normalizing names")
    if before != namespace_snapshot(stage, {}):
        raise ValueError("Material namespace normalization changed canonical values or connections")
    stage.GetRootLayer().Save()
    return renames


def derive_leaf(source, chunk, anchor, body_world0, output, source_file):
    """One exact body subtree, checked against independent source witnesses."""
    if output.exists():
        raise ValueError("Use a fresh body output directory")
    if any(path.is_symlink() or path.is_junction() for path in (output, *output.parents)):
        raise ValueError("Body output must not contain symlinks or junctions")
    output.mkdir(parents=True)
    owned_tree(output)
    for path in source.rglob("*"):
        if path.is_file() and path.relative_to(source).parts[0] == "textures":
            target = output / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    rows = [row for row in chunk["objects"] if anchor["visualRoot"] in row["ancestry"]]
    if not rows:
        raise ValueError("Source body has no mesh witnesses")
    expected = {chunk["nodes"][row["name"]] for row in rows}
    count = local_glb(source / "visual.glb", output / "visual.glb",
                      chunk["nodes"][anchor["visualRoot"]], expected)
    renames = local_stage(source / "asset.usdc", output / "asset.usdc", anchor["visualRoot"])
    scope = {"name": anchor["sourceBody"], "placement": body_world0, "nodes": chunk["nodes"],
             "objects": rows, "triangles": sum(row["triangles"] for row in rows)}
    audit = audit_visual(output, scope)
    group = anchor["bodyPrimPath"].split("/")[3]
    kind = "robot" if group == "panda_rail" else "labware" if group == "mother" else "device"
    metadata = {"id": kind + "/panda-body-" + re.sub("[^a-z0-9]+", "-", anchor["sourceBody"]),
        "kind": kind, "title": "Panda body-local visual " + anchor["sourceBody"], "stage": "asset.usdc",
        "visual": "visual.glb", "dressing": True, "dimensionsM": audit["dimensionsM"]}
    (output / "asset.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    (output / "LICENSE").write_text(LOCAL_AUTHORIZATION, encoding="utf-8")
    (output / "NOTICE").write_text(
        "Private source-derived appearance; no physics or workflow qualification.\n", encoding="utf-8")
    if chunk["kind"] == "robot" and chunk["name"].startswith("panda"):
        for name in ("LICENSE", "PANDA_README.md", "UPSTREAM_COMMIT.txt"):
            if (source / name).is_file():
                shutil.copyfile(source / name, output / ("PANDA_" + name))
    provenance = {"sourceFile": source_file, "frame": 0, "sourceBody": anchor["sourceBody"],
        "sourceRoot": anchor["visualRoot"], "bodyWorld0Column": body_world0,
        "sourceChunk": chunk["name"], "sourceChunkFiles": chunk["files"],
        "sourceMeshObjects": [row["name"] for row in rows], "shaderRenames": renames,
        "prototypeUnreachableGlbPayloadRetained": True,
        "files": {p.relative_to(output).as_posix(): fingerprint(p) for p in output.rglob("*") if p.is_file()}}
    write_json(output / "provenance.json", provenance)
    return {"bodyPath": anchor["bodyPrimPath"], "sourceRoot": anchor["visualRoot"],
            "bodyWorld0Column": body_world0, "sourceMeshObjects": len(rows),
            "renderedMeshes": count, "sourceChunk": chunk["name"], "audit": audit}


def build(project, derived, inventory_file, body_map_file, physics, workspace):
    if workspace.exists():
        raise ValueError("Use a fresh workspace")
    for path in (workspace, *workspace.parents):
        if path.is_symlink() or path.is_junction():
            raise ValueError("Workspace must not contain symlinks or junctions")
    if any(workspace.resolve().is_relative_to(path.resolve()) for path in (project, derived, physics)):
        raise ValueError("Workspace must stay outside all original inputs")
    receipt = json.loads((derived / "derivation.json").read_bytes())
    inventory, mapping = [json.loads(path.read_bytes()) for path in (inventory_file, body_map_file)]
    if receipt["recipe"] != RECIPE or receipt["frame"] != 0 or mapping["frame"] != 0:
        raise ValueError("Expected frozen final-lookdev frame0 inputs")
    if (inventory["sourceSha256"] != receipt["sourceFile"]["sha256"]
            or mapping["sourceFile"] != receipt["sourceFile"]):
        raise ValueError("Body map/inventory belong to another source")
    selected = [row for row in mapping["rows"] if row["dynamic"]]
    if len(selected) != 17 or len({row["bodyPrimPath"] for row in selected}) != 17:
        raise ValueError("Expected17 unique mapped moving bodies")
    used = {chunk["name"]: chunk for chunk in receipt["chunks"] if any(
        anchor["visualRoot"] in row["ancestry"] for anchor in selected for row in chunk["objects"])}
    preflight_packages(project, derived, receipt | {"chunks": list(used.values())})
    owned_tree(physics)
    if fingerprint(project / safe_relative(receipt["source"])) != receipt["sourceFile"]:
        raise ValueError("Original source no longer matches frozen geometry")
    objects = {row["name"]: row for row in inventory["objects"]}
    workspace.mkdir(parents=True)
    mappings, owners = {}, {}
    for anchor in selected:
        body = anchor["sourceBody"]
        path = anchor["bodyPrimPath"]
        if (not re.fullmatch(r"/World/Lab/(panda_rail|incubator|imager|mother)/[a-z0-9_]+", path)
                or path.rsplit("/", 1)[1] != body):
            raise ValueError("Body map must retain exact supported physical Prim Paths")
        group = path.split("/")[3].replace("_", "-")
        physical = Usd.Stage.Open(str(physics / "assets" / group / "asset.usdc"))
        if not physical or not physical.GetPrimAtPath("/Asset/" + body).HasAPI(UsdPhysics.RigidBodyAPI):
            raise ValueError("Body map does not identify an actual canonical rigid body")
        matches = [(chunk, [row for row in chunk["objects"] if anchor["visualRoot"] in row["ancestry"]])
                   for chunk in receipt["chunks"]]
        matches = [(chunk, rows) for chunk, rows in matches if rows]
        if len(matches) != 1:
            raise ValueError("Body meshes must belong to exactly one frozen source chunk")
        chunk, rows = matches[0]
        for row in rows:
            if row["name"] in owners:
                raise ValueError("Source mesh has duplicate body ownership: " + row["name"])
            owners[row["name"]] = body
        world = objects[anchor["visualRoot"]]["world"]
        mappings[body] = derive_leaf(derived / chunk["name"], chunk, anchor, world,
                                     workspace / "leaves" / body, receipt["sourceFile"])
    result = {"recipe": "worldbuilding-body-visuals/1", "sourceFile": receipt["sourceFile"],
        "physics": str(physics.resolve()), "mappings": mappings, "sourceMeshOwners": owners,
        "physicalFiles": {p.relative_to(physics).as_posix(): fingerprint(p)
                          for p in physics.rglob("*") if p.is_file()}}
    write_json(workspace / "body-visuals.json", result)
    return result


def compose(workspace, receipt, versions):
    owned_tree(workspace)
    physics = Path(receipt["physics"])
    owned_tree(physics)
    for name, expected in receipt["physicalFiles"].items():
        if fingerprint(physics / safe_relative(name)) != expected:
            raise ValueError("Physical input changed after visual build")
    output = workspace / "scene"
    if output.exists():
        raise ValueError("Use a fresh scene output")
    shutil.copytree(physics / "scene", output, copy_function=shutil.copyfile)
    layer = Sdf.Layer.FindOrOpen(str(output / "stage.usda"))
    for group in ("panda-rail", "incubator", "imager", "mother"):
        identity, rev = versions[group].rsplit("@", 1)
        layer.GetPrimAtPath("/World/Lab/" + group.replace("-", "_")).referenceList.prependedItems = [
            Sdf.Reference(f"library:assets/{identity}/{rev}/asset.usdc", "/Asset")]
    for body, row in receipt["mappings"].items():
        identity, rev = versions[body].rsplit("@", 1)
        prim = Sdf.CreatePrimInLayer(layer, row["bodyPath"])
        prim.referenceList.prependedItems = [
            Sdf.Reference(f"library:assets/{identity}/{rev}/asset.usdc", "/Asset")]
        Sdf.CreatePrimInLayer(layer, row["bodyPath"] + "/Appearance").SetInfo("active", False)
    layer.Save()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "install"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--derived", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--body-map", type=Path)
    parser.add_argument("--physics", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if args.action == "build":
        if any(getattr(args, field) is None for field in (
            "project", "derived", "inventory", "body_map", "physics"
        )):
            parser.error("build requires project, derived, inventory, body-map and physics")
        result = build(args.project, args.derived, args.inventory, args.body_map,
                       args.physics, args.workspace)
        print(json.dumps({"bodies": len(result["mappings"]), "meshes": len(result["sourceMeshOwners"])}))
    else:
        owned_tree(args.workspace)
        receipt = json.loads((args.workspace / "body-visuals.json").read_bytes())
        physics, versions, imports = Path(receipt["physics"]), {}, {}
        physical = [(name, physics / "assets" / name)
                    for name in ("panda-rail", "incubator", "imager", "mother")]
        visuals = [(name, args.workspace / "leaves" / name) for name in receipt["mappings"]]
        for key, directory in (*physical, *visuals):
            result = cli(args.url, "asset", "import", str(directory))
            imports[key] = result
            write_json(args.workspace / "imports.json", imports)
            if any(row["status"] == "fail" for row in result["validation"]["checks"]):
                raise RuntimeError("Asset validation failed: " + key)
            versions[key] = result["assetVersion"]
        scene = compose(args.workspace, receipt, versions)
        imports["scene"] = cli(args.url, "scene", "import", str(scene))
        write_json(args.workspace / "imports.json", imports)
        if any(row["status"] == "fail" for row in imports["scene"]["validation"]["checks"]):
            raise RuntimeError("Scene validation failed")
        print(imports["scene"]["sceneVersion"])


if __name__ == "__main__":
    main()
