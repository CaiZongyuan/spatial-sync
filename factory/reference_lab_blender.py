"""Derive bounded, static visual chunks from the supplied laboratory .blend.

Run inside Blender. The saved source and its embedded scripts are never changed
or executed. Robot chunks are visual reference data, not a physics replacement.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy


def select(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def is_robot(obj):
    while obj:
        if obj.get("source_commit") == "7aeeddc449edf8d62b594f9e36a81da53e7796f9":
            return True
        obj = obj.parent
    return False


def derive(source, output):
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    scene = next(item for item in bpy.data.scenes if item.name.startswith("01"))
    bpy.context.window.scene = scene
    groups, excluded = {"laboratory": [], "robot": []}, []
    for obj in list(scene.objects):
        if obj.type not in ("MESH", "CURVE", "FONT"):
            continue
        volume = any(material and material.node_tree and any(
            node.bl_idname == "ShaderNodeVolumePrincipled" for node in material.node_tree.nodes
        ) for material in obj.data.materials)
        reason = (
            "render-hidden" if obj.hide_render else
            "volume is not a core glTF material" if volume else
            "cutaway ceiling" if obj.name == "Continuous ceiling" else
            "photographic outdoor backdrop" if obj.name.startswith("Distant ")
            or obj.name == "Warm exterior sky backing" else None
        )
        if reason:
            excluded.append({"name": obj.name, "reason": reason})
            continue
        name, robot = obj.name, is_robot(obj)
        select([obj])
        # Evaluate each object's modifiers before joining; the original remains
        # in the source file, including the editable joint hierarchy.
        bpy.ops.object.convert(target="MESH")
        converted = bpy.context.object
        converted["source_object"] = name
        converted.data.calc_loop_triangles()
        count = len(converted.data.loop_triangles)
        if count > 350000:
            raise ValueError("Source object exceeds this recipe's chunk budget: " + name)
        groups["robot" if robot else "laboratory"].append((name, converted, count))
    output.mkdir(parents=True, exist_ok=True)
    chunks = []
    for group_name, objects in groups.items():
        batches, batch, triangles = [], [], 0
        for name, obj, count in sorted(objects, key=lambda row: row[0]):
            if batch and triangles + count > 350000:
                batches.append(batch)
                batch, triangles = [], 0
            batch.append((name, obj, count))
            triangles += count
        if batch:
            batches.append(batch)
        for index, batch in enumerate(batches, 1):
            identifier = f"{group_name}-{index}"
            select([row[1] for row in batch])
            if len(batch) > 1:
                bpy.ops.object.join()
            merged = bpy.context.object
            merged.name = identifier
            select([merged])
            filename = identifier + ".glb"
            bpy.ops.export_scene.gltf(
                filepath=str(output / filename), export_format="GLB", use_selection=True,
                use_active_scene=True, export_yup=False, export_animations=False,
                export_cameras=False, export_lights=False, export_materials="EXPORT",
            )
            stage_file = identifier + ".usdc"
            bpy.ops.wm.usd_export(
                filepath=str(output / stage_file), selected_objects_only=True,
                root_prim_path="/Asset", export_animation=False, export_materials=True,
                export_textures=False, export_lights=False, export_cameras=False,
            )
            value = (output / filename).read_bytes()
            chunks.append({"name": identifier, "group": group_name, "visual": filename,
                           "stage": stage_file,
                           "triangles": sum(row[2] for row in batch),
                           "objects": [row[0] for row in batch],
                           "sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)})
    receipt = {
        "recipe": "image-prompt-reference/1", "blender": bpy.app.version_string,
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "sourceScene": scene.name, "coordinates": "metres, Z-up", "chunks": chunks,
        "excluded": excluded,
        "approximations": [
            "Static visual reference; Blender joint controls are not exported as device behavior",
            "Procedural Noise/Bump, area lighting, volume and Cycles global illumination are not baked",
            "Viewer supplies its own local lighting; this is not a Cycles beauty-image reproduction",
        ],
    }
    (output / "derivation.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    derive(options.source.resolve(), options.output.resolve())
