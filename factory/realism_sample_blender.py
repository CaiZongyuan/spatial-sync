"""Offline CPU appearance experiment; run with Blender, never inside the gateway.

The request comes from tools/realism_sample.py. Outputs stay in its owned workspace.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np

CURRENT = ""


def material(name, color, roughness=.45, metallic=0., alpha=1., emission=None):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    shader = result.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, alpha)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Alpha"].default_value = alpha
    if alpha < 1:
        result.surface_render_method = "DITHERED"
    if emission:
        shader.inputs["Emission Color"].default_value = (*emission, 1)
        shader.inputs["Emission Strength"].default_value = .5
    return result


def finish(obj, name, mat, bevel=0.):
    obj.name = name
    obj["sample_asset"] = CURRENT
    obj.data.materials.append(mat)
    if bevel:
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        modifier = obj.modifiers.new("Manufactured edges", "BEVEL")
        modifier.width, modifier.segments = bevel, 3
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        for face in obj.data.polygons:
            face.use_smooth = True
        normal = obj.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
        bpy.ops.object.modifier_apply(modifier=normal.name)
    return obj


def box(name, center, size, mat, bevel=.002):
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.object
    obj.dimensions = size
    return finish(obj, name, mat, bevel)


def cylinder(name, center, radius, depth, mat, vertices=32):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=center)
    return finish(bpy.context.object, name, mat, min(.001, depth / 8))


def round_bottom_tube(mat):
    vertices = [(0, 0, 0)]
    profile = [(.008 * math.sin(n * math.pi / 12), .008 * (1 - math.cos(n * math.pi / 12)))
               for n in range(1, 7)] + [(.008, .094)]
    for radius, z in profile:
        vertices.extend((radius * math.cos(a * math.tau / 32), radius * math.sin(a * math.tau / 32), z)
                        for a in range(32))
    faces = [(0, 1 + (a + 1) % 32, 1 + a) for a in range(32)]
    for ring in range(len(profile) - 1):
        for a in range(32):
            start, end = 1 + ring * 32 + a, 1 + ring * 32 + (a + 1) % 32
            faces.append((start, end, end + 32, start + 32))
    mesh = bpy.data.meshes.new("Round-bottom glass wall")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new("Round-bottom sample tube", mesh)
    bpy.context.collection.objects.link(obj)
    for face in mesh.polygons:
        face.use_smooth = True
    return finish(obj, obj.name, mat)


def text(name, value, position, size, mat, rotation=(math.pi / 2, 0, 0)):
    bpy.ops.object.text_add(location=position, rotation=rotation)
    obj = bpy.context.object
    obj.data.body, obj.data.size, obj.data.extrude = value, size, .00008
    obj.name = name
    obj["sample_asset"] = CURRENT
    obj.data.materials.append(mat)
    bpy.ops.object.convert(target="MESH")
    return obj


def select(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def bake_surface(obj, destination):
    """Bake explicit color-only, tangent normal, roughness and self AO passes."""
    select([obj])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=.025)
    bpy.ops.object.mode_set(mode="OBJECT")
    source = obj.data.materials[0]
    nodes, links = source.node_tree.nodes, source.node_tree.links
    shader = nodes.get("Principled BSDF")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 95
    noise.inputs["Detail"].default_value = 2
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (.72, .75, .77, 1)
    ramp.color_ramp.elements[1].color = (.86, .89, .90, 1)
    links.new(noise.outputs["Fac"], ramp.inputs[0])
    links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = .22
    bump.inputs["Distance"].default_value = .00035
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    images = {}
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.samples = "CPU", 16
    scene.render.bake.margin = 12
    scene.render.bake.use_selected_to_active = False
    for name, kind, color_space in (
        ("basecolor", "DIFFUSE", "sRGB"),
        ("normal", "NORMAL", "Non-Color"),
        ("roughness", "ROUGHNESS", "Non-Color"),
        ("ao", "AO", "Non-Color"),
    ):
        image = bpy.data.images.new("case-" + name, width=512, height=512)
        image.colorspace_settings.name = color_space
        target = nodes.new("ShaderNodeTexImage")
        target.image = image
        nodes.active = target
        if kind == "DIFFUSE":
            scene.render.bake.use_pass_direct = False
            scene.render.bake.use_pass_indirect = False
            scene.render.bake.use_pass_color = True
        bpy.ops.object.bake(type=kind)
        image.filepath_raw = str(destination / (name + ".png"))
        image.file_format = "PNG"
        image.save()
        images[name] = image
        nodes.remove(target)
    # Metallic is an explicit constant, not an invented Blender bake pass.
    pixels = np.ones((512 * 512, 4), dtype=np.float32)
    for channel, name in ((0, "ao"), (1, "roughness")):
        source_pixels = np.empty(512 * 512 * 4, dtype=np.float32)
        images[name].pixels.foreach_get(source_pixels)
        pixels[:, channel] = source_pixels.reshape(-1, 4)[:, 0]
    pixels[:, 2] = .03
    orm = bpy.data.images.new("case-orm", width=512, height=512)
    orm.colorspace_settings.name = "Non-Color"
    orm.pixels.foreach_set(pixels.ravel())
    orm.filepath_raw, orm.file_format = str(destination / "orm.png"), "PNG"
    orm.save()
    result = material("Baked powder-coated housing", (1, 1, 1), metallic=1.)
    nodes, links = result.node_tree.nodes, result.node_tree.links
    shader = nodes.get("Principled BSDF")
    for name, socket in (("basecolor", "Base Color"),):
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = images[name]
        links.new(texture.outputs["Color"], shader.inputs[socket])
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = images["normal"]
    normal = nodes.new("ShaderNodeNormalMap")
    links.new(texture.outputs["Color"], normal.inputs["Color"])
    links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = orm
    separate = nodes.new("ShaderNodeSeparateColor")
    links.new(texture.outputs["Color"], separate.inputs["Color"])
    links.new(separate.outputs["Green"], shader.inputs["Roughness"])
    links.new(separate.outputs["Blue"], shader.inputs["Metallic"])
    group = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
    group.interface.new_socket(name="Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
    group.nodes.new("NodeGroupOutput")
    output = nodes.new("ShaderNodeGroup")
    output.node_tree = group
    links.new(separate.outputs["Red"], output.inputs["Occlusion"])
    obj.data.materials[0] = result
    return {name: {"min": float(np.min(np.asarray(image.pixels)[::4])),
                   "max": float(np.max(np.asarray(image.pixels)[::4]))}
            for name, image in images.items()}


def export_asset(name, output):
    objects = [obj for obj in bpy.context.scene.objects if obj.get("sample_asset") == name]
    # Merge only within the same asset/material; physics identities stay outside Blender.
    materials = {}
    for obj in objects:
        materials.setdefault(obj.data.materials[0], []).append(obj)
    for group in materials.values():
        if len(group) > 1:
            select(group)
            bpy.ops.object.join()
    objects = [obj for obj in bpy.context.scene.objects if obj.get("sample_asset") == name]
    select(objects)
    bpy.ops.export_scene.gltf(filepath=str(output / (name + ".glb")), export_format="GLB",
                              use_selection=True, export_yup=False, export_animations=False,
                              export_materials="EXPORT", export_cameras=False, export_lights=False)


def build(request, output):
    global CURRENT
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    white = material("Warm laboratory enamel", (.78, .81, .80), .32)
    steel = material("Brushed stainless steel", (.49, .53, .56), .28, .85)
    dark = material("Graphite polymer", (.035, .045, .05), .52)
    teal = material("Anodized teal", (.025, .26, .30), .34, .45)
    blue = material("Blue screw caps", (.025, .18, .45), .32)
    glass = material("Simplified tube glass", (.72, .86, .88), .14, alpha=.22)
    fluid = material("Simplified sample liquid", (.055, .38, .50), .22)
    screen = material("Instrument display", (.005, .02, .025), .28, emission=(.03, .32, .36))
    label = material("Printed labels", (.75, .84, .83), .7)

    CURRENT = "instrument"
    housing = box("Powder-coated instrument housing", (0, 0, .125), (.44, .37, .25), white.copy(), .018)
    lip = box("Raised upper shell", (0, 0, .244), (.41, .34, .026), housing.data.materials[0], .006)
    select([housing, lip])
    bpy.ops.object.join()
    receipts = bake_surface(housing, output)
    box("Control panel", (0, -.184, .10), (.32, .008, .115), dark, .008)
    box("LCD glass", (-.04, -.190, .12), (.15, .003, .047), screen, .003)
    text("Readout", "04:30   READY", (-.107, -.193, .113), .012, label)
    text("Instrument identity", "LAB / CENTRIFUGE", (-.175, -.191, .035), .011, steel)
    knob = cylinder("Control encoder", (.126, -.197, .117), .020, .015, steel)
    knob.rotation_euler.x = math.pi / 2
    cylinder("Lid gasket", (0, .025, .254), .135, .009, dark, 64)
    cylinder("Lid cover", (0, .025, .263), .129, .013, white, 64)
    cylinder("Lid viewing window", (0, .025, .272), .077, .006, dark, 64)
    cylinder("Lid window glass", (0, .025, .276), .072, .003, glass, 64)
    for index in range(8):
        box("Vent slot", (.221, -.11 + index * .027, .08), (.002, .014, .065), dark, .0006)
    for x in (-.17, .17):
        for y in (-.13, .13):
            cylinder("Rubber instrument foot", (x, y, .006), .018, .012, dark)

    CURRENT = "bench"
    box("Solid worktop", (0, 0, .7975), (1.9, .90, .055), white, .006)
    box("Stainless work surface", (0, 0, .826), (1.89, .89, .004), steel, .002)
    for x in (-.82, .82):
        for y in (-.34, .34):
            box("Bench leg", (x, y, .385), (.048, .048, .77), steel, .003)
            box("Adjustable foot", (x, y, .018), (.065, .065, .036), dark, .004)
    for x in (-.62, .60):
        box("Cabinet carcass", (x, .04, .42), (.43, .68, .57), white, .007)
        for z in (.30, .49, .68):
            box("Drawer front", (x, -.307, z), (.41, .020, .174), white, .003)
            box("Drawer pull", (x, -.326, z + .045), (.20, .022, .012), steel, .003)
    box("Rear service rail", (0, .425, .89), (1.88, .035, .105), white, .004)
    for x in (-.70, -.59, -.48):
        box("Service outlet", (x, .404, .90), (.065, .009, .055), dark, .003)

    CURRENT = "rack"
    mesh = bpy.data.meshes.new("Authored holes from canonical rack recipe")
    mesh.from_pydata(request["rackMesh"]["vertices"], [], request["rackMesh"]["faces"])
    mesh.update()
    obj = bpy.data.objects.new("Six-hole rack", mesh)
    bpy.context.collection.objects.link(obj)
    obj["sample_asset"] = CURRENT
    obj.data.materials.append(teal)
    text("Rack identity", "RACK / 06", (-.065, -.050, .018), .009, label)

    CURRENT = "tube"
    round_bottom_tube(glass)
    cylinder("Sample liquid", (0, 0, .024), .0064, .043, fluid)
    cylinder("Screw cap", (0, 0, .096), .009, .014, blue)
    for z in (.030, .045, .060, .075):
        box("Printed graduation", (.0, -.008, z), (.005, .0003, .0006), label, 0)
    for index in range(12):
        angle = index * math.tau / 12
        box("Cap grip", (.0085 * math.cos(angle), .0085 * math.sin(angle), .096),
            (.0012, .0012, .011), blue, .0003)

    CURRENT = "room"
    tile = material("Porcelain floor", (.48, .51, .50), .64)
    wall = material("Painted laboratory walls", (.76, .79, .77), .8)
    for x in range(6):
        for y in range(4):
            box("Floor tile", (-1.25 + x * .5, -.75 + y * .5, -.014), (.495, .495, .028), tile, .001)
    box("Rear wall", (0, .99, .80), (3., .04, 1.60), wall, .002)
    box("Left return wall", (-1.49, .45, .80), (.04, 1.1, 1.60), wall, .002)
    box("Rear baseboard", (0, .959, .045), (2.96, .018, .09), steel, .002)
    for x in (-.90, -.28, .34, .96):
        box("Wall cabinet", (x, .825, 1.28), (.58, .28, .42), white, .006)
        box("Cabinet door", (x, .677, 1.28), (.55, .016, .39), white, .003)
        box("Cabinet handle", (x + .19, .66, 1.22), (.012, .022, .13), steel, .002)
    text("Wall identity", "LAB WORLD / SAMPLE PREPARATION", (-1.10, .96, .99), .045, dark)
    for name in ("bench", "instrument", "rack", "tube", "room"):
        export_asset(name, output)
    # Save an asset workspace with one preview instance of each authored visual.
    # The canonical scene adds the trusted robot and the remaining tube instances.
    offsets = {"instrument": (.56, .12, .828), "rack": (.20, -.20, .828),
               "tube": (.155, -.2175, .834)}
    for obj in bpy.context.scene.objects:
        offset = offsets.get(obj.get("sample_asset"), (0, 0, 0))
        obj.location.x += offset[0]
        obj.location.y += offset[1]
        obj.location.z += offset[2]
    bpy.ops.wm.save_as_mainfile(filepath=str(output / "workbench.blend"))
    files = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
             for p in sorted(output.iterdir()) if p.suffix in (".glb", ".png", ".blend")}
    receipt = {"blender": bpy.app.version_string, "device": "CPU", "samples": 16,
               "bakeSize": [512, 512], "normalSpace": "TANGENT", "bakeStats": receipts,
               "metallic": {"mode": "constant", "value": .03},
               "bakeOccluders": "instrument housing only; before other scene objects exist",
               "coordinates": "meter, Z-up; export_yup=False", "files": files}
    (output / "appearance-build.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print("REALISM_BAKE " + json.dumps(receipt))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    build(json.loads(Path(args.request).read_text(encoding="utf-8")), output)
