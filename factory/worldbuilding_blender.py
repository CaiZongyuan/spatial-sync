"""Export the supplied World Building laboratory as local static visual Assets.

Run with Blender background Python. No source file is saved, no render is run,
and the source's animations, collision objects and device behavior are not exported.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.visual_geometry import surface_signature  # noqa: E402

RECIPE = "worldbuilding-cell-passaging-visuals/2"
TRIANGLE_BUDGET = 450000
ROOT_GROUPS = {
    "ROOT_cabinet": ("incubator", "device"),
    "ROOT_outer_door": ("incubator", "device"),
    "ROOT_inner_door": ("incubator", "device"),
    "ROOT_inc": ("incubator", "device"),
    "housing_ROOT": ("imager", "device"),
    "drawer_ROOT": ("imager", "device"),
    "base_fixed": ("workstation", "device"),
    "X_carriage_joint": ("workstation", "device"),
    "Y_carriage_joint": ("workstation", "device"),
    "v03_Z_joint": ("workstation", "device"),
    "v03_fixed_head_root": ("workstation", "device"),
    "front_door_left_hinge": ("workstation", "device"),
    "front_door_right_hinge": ("workstation", "device"),
    "anchored_base": ("centrifuge", "device"),
    "rotor": ("centrifuge", "device"),
    "LID_hinge_0_to_100deg": ("centrifuge", "device"),
    "LUMEN_TECH_CENTRIFUGE_V03_P0": ("centrifuge", "device"),
    "plate_ROOT": ("mother-plate", "labware"),
    "lid_ROOT": ("mother-lid", "labware"),
    "plate_ROOT__daughter": ("daughter-plate", "labware"),
    "lid_ROOT__daughter": ("daughter-lid", "labware"),
    "plate_ROOT__count": ("count-plate", "labware"),
}
ANCHORS = {
    "panda": "ROOT_panda__link0", "rail": "ROOT_rail_beam",
    "incubator": "ROOT_cabinet", "imager": "housing_ROOT",
    "workstation": "base_fixed", "centrifuge": "anchored_base", "decapper": "ROOT_dc",
    **{group: root for root, (group, _) in ROOT_GROUPS.items() if group.endswith(("plate", "lid"))},
}
REQUIRED_GROUPS = {
    "panda", "rail", "incubator", "imager", "workstation", "centrifuge", "decapper",
    "mother-plate", "mother-lid", "daughter-plate", "daughter-lid", "count-plate",
}


def classify(ancestry):
    """The named assembly roots come from this source's build_render_scene.py."""
    for name in ancestry:
        if name in ROOT_GROUPS:
            return ROOT_GROUPS[name]
        if name.startswith("ROOT_panda__"):
            return "panda", "robot"
        if name.startswith("ROOT_rail_"):
            return "rail", "device"
        if name.startswith("ROOT_dc"):
            return "decapper", "device"
        if name.startswith(("ROOT_tube_", "ROOT_cap_", "ROOT_tip_")):
            return name.removeprefix("ROOT_").lower().replace("_", "-"), "labware"
        if name == "ROOT_ws_labware":
            return "workstation-labware", "labware"
        if name == "ROOT_sr":
            return "tube-rack", "labware"
        if name == "ROOT_waste":
            return "waste", "device"
    return "environment", "environment"


def batches(rows, budget=TRIANGLE_BUDGET):
    result, current, total = [], [], 0
    for row in sorted(rows, key=lambda item: item["name"]):
        if row["triangles"] > budget:
            raise ValueError("Source object exceeds visual chunk budget: " + row["name"])
        if current and total + row["triangles"] > budget:
            result.append(current)
            current, total = [], 0
        current.append(row)
        total += row["triangles"]
    if current:
        result.append(current)
    return result


def fingerprint(path):
    value = path.read_bytes()
    return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}


def matrix_rows(matrix):
    return [list(row) for row in matrix]


def derive(project, output):
    import bpy
    import numpy as np
    from mathutils import Matrix

    source = project / "07-render/render_scene_lookdev.blend"
    before = fingerprint(source)
    if output.exists():
        raise ValueError("Use a fresh output directory; generated packages are immutable")
    output.mkdir(parents=True)
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    scene.frame_set(0)
    bpy.context.view_layer.update()
    if any(lib.filepath for lib in bpy.data.libraries):
        raise ValueError("Linked Blender libraries must be made self-contained before intake")
    images, texture_conversions = [], []
    for image in list(bpy.data.images):
        if image.type in ("RENDER_RESULT", "COMPOSITING"):
            continue
        if not image.packed_file:
            raise ValueError("Unpacked image dependency: " + image.name)
        data = bytes(image.packed_file.data)
        images.append({"name": image.name, "sha256": hashlib.sha256(data).hexdigest(),
                       "bytes": len(data), "dimensions": list(image.size), "packed": True})
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            # Core glTF uses PNG/JPEG. Preserve alpha without adding the WebP
            # extension/decoder or depending on an external source filepath.
            replacement = bpy.data.images.new(image.name + "_png", *image.size, alpha=True)
            replacement.colorspace_settings.name = image.colorspace_settings.name
            pixels = np.empty(len(image.pixels), dtype=np.float32)
            image.pixels.foreach_get(pixels)
            replacement.pixels.foreach_set(pixels)
            replacement.pack()
            png = bytes(replacement.packed_file.data)
            if not png.startswith(b"\x89PNG"):
                raise ValueError("Packed texture conversion did not produce PNG")
            for material in bpy.data.materials:
                if material.node_tree:
                    for node in material.node_tree.nodes:
                        if node.type == "TEX_IMAGE" and node.image == image:
                            node.image = replacement
            texture_conversions.append({"source": image.name, "output": replacement.name,
                                        "format": "PNG", "sha256": hashlib.sha256(png).hexdigest()})
    material_adaptations = []
    for material in bpy.data.materials:
        tree = material.node_tree
        if not tree:
            continue
        transparent = next((node for node in tree.nodes if node.type == "BSDF_TRANSPARENT"), None)
        if transparent is None or any(node.type == "BSDF_PRINCIPLED" for node in tree.nodes):
            continue
        diffuse = next((node for node in tree.nodes if node.type == "BSDF_DIFFUSE"), None)
        color = tuple((diffuse or transparent).inputs["Color"].default_value)
        body_mix = next((node for node in tree.nodes if node.type == "MIX_SHADER"
                         and not node.inputs[0].is_linked), None)
        opacity = min(.6, max(.08, .08 + body_mix.inputs[0].default_value)) if body_mix else .12
        material_adaptations.append({"sourceMaterial": material.name,
                                     "sourceShaderNodes": sorted(node.type for node in tree.nodes),
                                     "recipe": "core-PBR-alpha-approximation", "rgba": [*color[:3], opacity],
                                     "roughness": .12})
        # Fresnel/Transparent/Glossy mixes have no core-glTF representation and
        # otherwise export as opaque white, hiding the equipment behind glass.
        tree.nodes.clear()
        shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
        shader.inputs["Base Color"].default_value = (*color[:3], 1)
        shader.inputs["Alpha"].default_value = opacity
        shader.inputs["Roughness"].default_value = .12
        output_node = tree.nodes.new("ShaderNodeOutputMaterial")
        tree.links.new(shader.outputs["BSDF"], output_node.inputs["Surface"])
        material.surface_render_method = "DITHERED"
    depsgraph = bpy.context.evaluated_depsgraph_get()
    groups, excluded, objects = {}, [], {}
    for obj in scene.objects:
        if obj.type not in ("MESH", "CURVE", "FONT"):
            continue
        ancestry, parent = [], obj
        while parent:
            ancestry.append(parent.name)
            parent = parent.parent
        reason = ("collision/helper object" if any(
            "COL_" in name or name.lower().startswith("col_") or "collision" in name.lower()
            for name in ancestry
        ) else "hidden from source render" if obj.hide_render else
            "source frame-0 camera cutaway" if not obj.visible_camera else
            "browser cutaway ceiling/exterior wall" if obj.name == "LD_ceiling"
            or obj.name.startswith(("LD_panel_", "LD_nb_wall_")) else None)
        if reason:
            excluded.append({"name": obj.name, "reason": reason})
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        triangles = len(mesh.loop_triangles)
        coordinates = np.empty((len(mesh.vertices), 3), dtype=np.float64)
        mesh.vertices.foreach_get("co", coordinates.reshape(-1))
        indices = np.empty((triangles, 3), dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", indices.reshape(-1))
        vertices = coordinates[indices]
        zero_area = int(np.count_nonzero(np.all(
            np.cross(vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]) == 0, axis=1)))
        if triangles == zero_area:
            evaluated.to_mesh_clear()
            excluded.append({"name": obj.name, "reason": "no nondegenerate surface"})
            continue
        triangle_geometry, surface_points = surface_signature(
            coordinates, [3] * triangles, indices.reshape(-1))
        polygon_counts = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("loop_total", polygon_counts)
        polygon_indices = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", polygon_indices)
        polygon_geometry, _ = surface_signature(coordinates, polygon_counts, polygon_indices)
        evaluated.to_mesh_clear()
        if triangles == 0:
            excluded.append({"name": obj.name, "reason": "no evaluated triangles"})
            continue
        group, kind = classify(ancestry)
        world = np.asarray(evaluated.matrix_world)
        world_points = surface_points @ world[:3, :3].T + world[:3, 3]
        row = {"name": obj.name, "parent": obj.parent.name if obj.parent else None,
               "ancestry": ancestry, "triangles": triangles, "kind": kind,
               "nonDegenerateTriangles": triangles - zero_area, "zeroAreaTriangles": zero_area,
               "sourceTriangleGeometry": triangle_geometry, "sourcePolygonGeometry": polygon_geometry,
               "sourceWorld": matrix_rows(evaluated.matrix_world),
               "sourceWorldBounds": [world_points.min(axis=0).tolist(), world_points.max(axis=0).tolist()]}
        groups.setdefault(group, []).append(row)
        objects[obj.name] = obj
    missing = REQUIRED_GROUPS - groups.keys()
    if missing:
        raise ValueError("Missing major source assemblies: " + ", ".join(sorted(missing)))
    chunks = []
    for group, rows in sorted(groups.items()):
        anchor_name = ANCHORS.get(group)
        anchor = scene.objects[anchor_name].matrix_world.copy() if anchor_name else Matrix.Identity(4)
        # Keep rigid placement in the Scene; mesh vertices/roots remain local to
        # the source assembly, including individual moving-body anchor nodes.
        if any(abs(value - 1) > 1e-6 for value in anchor.to_scale()):
            raise ValueError("An assembly anchor must have unit scale: " + group)
        for index, rows_in_chunk in enumerate(batches(rows), 1):
            name = f"{group}-{index}"
            directory = output / name
            directory.mkdir()
            export_scene = bpy.data.scenes.new("Intake_" + name)
            bpy.context.window.scene = export_scene
            clones = {}
            names_in_chunk = {row["name"] for row in rows_in_chunk}

            def clone(original):
                if original.name in clones:
                    return clones[original.name]
                parent = clone(original.parent) if original.parent else None
                data = (bpy.data.meshes.new_from_object(original.evaluated_get(depsgraph))
                        if original.name in names_in_chunk else None)
                result = bpy.data.objects.new(original.name + "__intake", data)
                result["source_object"] = original.name
                export_scene.collection.objects.link(result)
                result.parent = parent
                result.matrix_world = anchor.inverted() @ original.matrix_world
                clones[original.name] = result
                return result

            for row in rows_in_chunk:
                clone(objects[row["name"]])
            bpy.context.view_layer.update()
            for obj in export_scene.objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = next(iter(export_scene.objects))
            bpy.ops.export_scene.gltf(
                filepath=str(directory / "visual.glb"), export_format="GLB", use_selection=True,
                use_active_scene=True, export_yup=False, export_animations=False, export_extras=True,
                export_cameras=False, export_lights=False, export_materials="EXPORT",
            )
            bpy.ops.wm.usd_export(
                filepath=str(directory / "asset.usdc"), selected_objects_only=True,
                root_prim_path="/Asset", export_animation=False, export_materials=True,
                export_textures_mode="NEW", export_lights=False, export_cameras=False,
                convert_world_material=False,
            )
            chunks.append({"name": name, "group": group, "kind": rows_in_chunk[0]["kind"],
                           "anchor": anchor_name, "placement": matrix_rows(anchor),
                           "triangles": sum(row["triangles"] for row in rows_in_chunk),
                           "objects": rows_in_chunk,
                           "nodes": {key: value.name for key, value in clones.items()},
                           "files": {path.relative_to(directory).as_posix(): fingerprint(path)
                                     for path in sorted(directory.rglob("*")) if path.is_file()}})
            bpy.context.window.scene = scene
            for obj in list(export_scene.objects):
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data is not None and data.users == 0:
                    bpy.data.meshes.remove(data)
            bpy.data.scenes.remove(export_scene)
            print("EXPORTED", name, chunks[-1]["triangles"], flush=True)
    if fingerprint(source) != before:
        raise ValueError("Source changed during export; discard this derivative")
    receipt = {
        "recipe": RECIPE, "blender": bpy.app.version_string,
        "blenderBuild": bpy.app.build_hash.decode(), "frame": 0,
        "coordinates": "metres, Z-up; no up-axis conversion",
        "source": "07-render/render_scene_lookdev.blend", "sourceFile": before,
        "producer": {"path": "factory/worldbuilding_blender.py", **fingerprint(Path(__file__))},
        "geometryWitnessProducer": {"path": "factory/visual_geometry.py",
                                    **fingerprint(Path(__file__).with_name("visual_geometry.py"))},
        "packedImages": images, "textureConversions": texture_conversions,
        "materialAdaptations": material_adaptations,
        "externalLibraries": [], "chunks": chunks, "excluded": excluded,
        "approximations": [
            "Static evaluated frame 0; no animation, physics, actions or workflow qualification",
            "Ceiling, ceiling panel display meshes and neighboring exterior walls are omitted "
            "for the browser's external cutaway view; all omitted source names are recorded",
            "Only core glTF/USD Preview Surface material features; procedural shaders, thin-glass mixes, "
            "Cycles lighting and volume are not baked",
            "Transparent/Glossy mixes use recorded core-PBR alpha approximations; "
            "the procedural blue plate band becomes an opaque blue plate",
            "Source centrifuge has eight visible adapters; physical loading qualification "
            "is only adapters 2/6",
            "Mixed-source license/distribution permission remains unverified; local user-library use only",
        ],
    }
    (output / "derivation.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    derive(args.project.resolve(), args.output.resolve())
