"""Blender 4.5.9 background authoring entry for the Scene Factory.

Run through Blender's own Python (never imported by the gateway):

    blender --background --factory-startup --python factory/blender_author.py -- \
      --request .scratch/sf3/blender/request.json \
      --blend .scratch/sf3/blender/tube-rack-demo.blend \
      --layout .scratch/sf3/blender/layout.json

A saved, human-edited workspace is exported through the same entrypoint:

    blender --background --python factory/blender_author.py -- \
      --request .scratch/sf3/blender/request.json \
      --open-blend .scratch/sf3/blender/tube-rack-demo.blend \
      --layout .scratch/sf3/blender/layout-human.json

Trusted USD assets are imported only as visual references. Blender exports no
USD: the layout manifest carries poses and stable instance identity back to the
pure package generator, which writes the OpenUSD composition and ``@library:``
references. Mesh edits are rejected instead of silently dropped.

The MCP for Blender server is for interactive inspection only and is not started
here. When it is used, set ``BLENDER_MCP_SAFE_MODE=1`` and
``DISABLE_TELEMETRY=true``; do not enable auto-start or third-party providers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

BLENDER_VERSION = "4.5.9"
SCHEMA = "lw.scene-layout/1"
REQUEST_SCHEMA = "lw.blender-authoring/1"
COLLECTION = "SceneFactory"


class AuthoringError(RuntimeError):
    pass


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Pure-factory authoring request JSON")
    parser.add_argument("--blend", help="Create or replace this .blend workspace")
    parser.add_argument("--open-blend", help="Open a saved human-edited .blend and extract its layout")
    parser.add_argument("--layout", required=True, help="Write the layout manifest here")
    parser.add_argument(
        "--mode",
        choices=("script", "human-edited"),
        help="Recorded authoring path; inferred from --open-blend when omitted",
    )
    args = parser.parse_args(argv)
    if bool(args.blend) == bool(args.open_blend):
        parser.error("provide exactly one of --blend or --open-blend")
    return args


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _request_digest(request):
    payload = {key: value for key, value in request.items() if key != "requestSha256"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check_version():
    version = ".".join(str(part) for part in bpy.app.version[:3])
    if version != BLENDER_VERSION:
        raise AuthoringError(f"Blender {BLENDER_VERSION} is required, found {version}")


def _activate(collection):
    layer = bpy.context.view_layer.layer_collection.children.get(collection.name)
    if layer is None:
        raise AuthoringError("cannot activate the factory collection")
    bpy.context.view_layer.active_layer_collection = layer


def _factory_collection(scene):
    collection = bpy.data.collections.get(COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(COLLECTION)
        scene.collection.children.link(collection)
    return collection


def _create_empty(collection, instance):
    empty = bpy.data.objects.new(f"factory:{instance['instanceId']}", None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.empty_display_size = 0.04
    empty.location = tuple(float(value) for value in instance["positionM"])
    empty.rotation_mode = "XYZ"
    empty.rotation_euler = tuple(math.radians(float(value)) for value in instance["rotationDeg"])
    empty["factory_instance_id"] = instance["instanceId"]
    empty["factory_entity_key"] = instance["entityKey"]
    collection.objects.link(empty)
    return empty


def _import_trusted(collection, empty, instance):
    stage = Path(instance["stagePath"])
    if not stage.is_file():
        raise AuthoringError(f"trusted asset stage is missing: {stage}")
    before = set(bpy.data.objects)
    _activate(collection)
    bpy.ops.wm.usd_import(filepath=str(stage))
    created = [obj for obj in bpy.data.objects if obj not in before]
    if not created:
        raise AuthoringError(f"Blender imported no objects from {stage.name}")
    for obj in created:
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
    for obj in created:
        if obj.parent is None:
            obj.parent = empty
            obj.matrix_parent_inverse = Matrix.Identity(4)


def _create_procedural(collection, empty, instance):
    mesh_data = instance["visualMesh"]
    mesh = bpy.data.meshes.new(f"factory:{instance['instanceId']}:mesh")
    mesh.from_pydata(
        [tuple(vertex) for vertex in mesh_data["vertices"]],
        [],
        [tuple(face) for face in mesh_data["faces"]],
    )
    mesh.validate(verbose=False)
    mesh.update()
    obj = bpy.data.objects.new(f"factory:{instance['instanceId']}:mesh", mesh)
    collection.objects.link(obj)
    obj.parent = empty
    obj.matrix_parent_inverse = Matrix.Identity(4)
    material = bpy.data.materials.get("Factory_Preview")
    if material is None:
        material = bpy.data.materials.new("Factory_Preview")
        material.diffuse_color = (0.22, 0.42, 0.58, 1.0)
    mesh.materials.append(material)


def _create_dressing(collection, item):
    size = item["sizeM"]
    mesh = bpy.data.meshes.new(f"factory:dressing:{item['name']}:mesh")
    mesh.from_pydata(
        [(-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
         (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)],
        [],
        [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
    )
    mesh.validate(verbose=False)
    mesh.update()
    obj = bpy.data.objects.new(f"factory:dressing:{item['name']}", mesh)
    collection.objects.link(obj)
    obj.location = tuple(float(value) for value in item["positionM"])
    obj.scale = tuple(float(value) for value in size)
    material = bpy.data.materials.new(f"Factory_Dressing_{item['name']}")
    material.diffuse_color = tuple(item.get("color") or [0.55, 0.58, 0.61]) + (1.0,)
    mesh.materials.append(material)


def _create_workspace(request, blend):
    _check_version()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene["factory_recipe"] = request["recipe"]
    scene["factory_plan_sha256"] = request["planSha256"]
    scene["factory_request_sha256"] = _request_digest(request)
    collection = _factory_collection(scene)
    for instance in request["instances"]:
        empty = _create_empty(collection, instance)
        if instance["kind"] == "trusted":
            _import_trusted(collection, empty, instance)
        else:
            _create_procedural(collection, empty, instance)
    for item in request.get("dressing") or []:
        _create_dressing(collection, item)
    Path(blend).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(blend).resolve()))


def _open_workspace(request, blend):
    _check_version()
    path = Path(blend)
    if not path.is_file():
        raise AuthoringError(f"saved Blender workspace does not exist: {path}")
    bpy.ops.wm.open_mainfile(filepath=str(path.resolve()))
    scene = bpy.context.scene
    saved_plan = scene.get("factory_plan_sha256")
    if saved_plan != request["planSha256"]:
        raise AuthoringError(
            "saved Blender workspace belongs to another plan version; open the factory's current plan"
        )


def _local_bounds(empty):
    inverse = empty.matrix_world.inverted()
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    meshes = [obj for obj in empty.children_recursive if obj.type == "MESH"]
    for obj in meshes:
        relative = inverse @ obj.matrix_world
        for corner in obj.bound_box:
            point = relative @ Vector(tuple(corner))
            for axis in range(3):
                minimum[axis] = min(minimum[axis], point[axis])
                maximum[axis] = max(maximum[axis], point[axis])
    if not meshes:
        raise AuthoringError(f"instance {empty.name} has no visual mesh")
    return [minimum, maximum]


def _bounds_match(actual, expected, tolerance=1e-4):
    return all(
        len(expected) == 2
        and all(
            len(expected[row]) == 3
            and abs(float(actual[row][axis]) - float(expected[row][axis])) <= tolerance
            for axis in range(3)
        )
        for row in range(2)
    )


def _clean(value):
    number = round(float(value), 6)
    return 0.0 if number == 0 else number


def _extract_layout(request, mode, blend, blend_sha):
    instances, procedural = [], []
    for instance in request["instances"]:
        name = f"factory:{instance['instanceId']}"
        empty = bpy.data.objects.get(name)
        if empty is None or empty.type != "EMPTY":
            raise AuthoringError(f"saved workspace is missing the factory instance empty: {name}")
        location, rotation, scale = empty.matrix_world.decompose()
        if max(abs(scale[axis] - 1.0) for axis in range(3)) > 1e-6:
            raise AuthoringError(
                "scaled instances are not supported; derive a new asset version instead"
            )
        euler = rotation.to_euler("XYZ")
        position = [_clean(value) for value in location]
        rotation_deg = [_clean(math.degrees(value)) for value in euler]
        instances.append(
            {
                "instanceId": instance["instanceId"],
                "entityKey": instance["entityKey"],
                "positionM": position,
                "rotationDeg": rotation_deg,
            }
        )
        if instance["kind"] == "procedural":
            bounds = _local_bounds(empty)
            if not _bounds_match(bounds, instance.get("localBoundsM")):
                raise AuthoringError(
                    f"mesh in {name} differs from the factory parameters; "
                    "change the plan parameters and build a new asset version"
                )
            entry = {
                "instanceId": instance["instanceId"],
                "type": instance["type"],
                "dimensionsM": list(instance["dimensionsM"]),
                "localBoundsM": bounds,
                "seed": instance.get("seed", 0),
            }
            if instance["type"] == "six-hole-tube-rack":
                entry |= {
                    "holeCount": instance["holeCount"],
                    "collision": instance["collision"],
                }
            procedural.append(entry)
    return {
        "schema": SCHEMA,
        "recipe": request["recipe"],
        "scene": request["scene"]["name"],
        "planSha256": request["planSha256"],
        "requestSha256": _request_digest(request),
        "mode": mode,
        "blender": {
            "version": ".".join(str(part) for part in bpy.app.version[:3]),
            "blendSha256": blend_sha,
            "file": Path(blend).name,
        },
        "units": {"length": "m", "upAxis": "Z", "metersPerUnit": 1},
        "instances": instances,
        "procedural": procedural,
    }


def main(argv):
    args = parse_args(argv)
    request = _read_json(args.request)
    if request.get("schema") != REQUEST_SCHEMA:
        raise AuthoringError("authoring request has an unsupported schema")
    mode = args.mode or ("human-edited" if args.open_blend else "script")
    if args.open_blend:
        _open_workspace(request, args.open_blend)
        blend = args.open_blend
    else:
        _create_workspace(request, args.blend)
        blend = args.blend
    layout = _extract_layout(request, mode, blend, _file_digest(blend))
    _write_json(args.layout, layout)
    print(
        "SF3_LAYOUT "
        + json.dumps(
            {
                "layout": str(args.layout),
                "mode": mode,
                "scene": layout["scene"],
                "instances": len(layout["instances"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    try:
        raise SystemExit(main(arguments))
    except AuthoringError as error:
        print("SF3_ERROR " + json.dumps({"message": str(error)}, sort_keys=True))
        raise SystemExit(1) from error
