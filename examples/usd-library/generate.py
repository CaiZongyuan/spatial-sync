"""Generate the small, self-authored USD/PBR packages used by the loading example."""

import hashlib
import json
from pathlib import Path

import trimesh
import yaml
from trimesh.visual.material import PBRMaterial

ROOT = Path(__file__).resolve().parent
HEADER = '#usda 1.0\n(defaultPrim = "Asset"\n metersPerUnit = 1\n upAxis = "Z")\n'


def pbr(mesh, color, *, metallic=0., roughness=.65):
    mesh.visual = trimesh.visual.TextureVisuals(material=PBRMaterial(
        baseColorFactor=color, metallicFactor=metallic, roughnessFactor=roughness,
    ))
    return mesh


def mesh_usd(mesh):
    points = ", ".join("(" + ", ".join(f"{value:.8g}" for value in point) + ")" for point in mesh.vertices)
    faces = ", ".join(str(int(index)) for face in mesh.faces for index in face)
    counts = ", ".join(["3"] * len(mesh.faces))
    return (f' point3f[] points = [{points}]\n int[] faceVertexCounts = [{counts}]\n'
            f' int[] faceVertexIndices = [{faces}]\n uniform token subdivisionScheme = "none"\n')


def asset(identifier, title, usd, mesh, **extra):
    kind, name = identifier.split("/")
    destination = ROOT / "assets" / name
    destination.mkdir(parents=True, exist_ok=True)
    metadata = {"id": identifier, "kind": kind, "title": title, "stage": "asset.usda",
                "visual": "visual.glb", "description": "Authored loading fixture; not a production device.",
                **extra}
    files = {"asset.yaml": yaml.safe_dump(metadata, allow_unicode=True).encode(),
             "asset.usda": (HEADER + usd).encode(), "visual.glb": trimesh.Scene(mesh).export(file_type="glb"),
             "LICENSE.txt": (b"CC0-1.0. Self-authored geometry and materials "
                             b"for spatial-sync loading tests.\n")}
    files["provenance.json"] = json.dumps({
        "source": "examples/usd-library/generate.py", "license": "CC0-1.0",
        "files": {name: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
                  for name, value in files.items()},
    }, indent=2).encode()
    for name, value in files.items():
        (destination / name).write_bytes(value)


def generate():
    tube = pbr(trimesh.creation.cylinder(radius=.02, height=.12, sections=24), [67, 123, 168, 255],
               metallic=.15, roughness=.32)
    asset("labware/usd-fixture-tube", "USD fixture tube", '''
def Cylinder "Asset" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"]) {
 double radius = 0.02
 double height = 0.12
 uniform token axis = "Z"
 float physics:mass = 0.1
}
''', tube, dimensionsM=[.04, .04, .12],
          physics={"massKg": .1, "massSource": "estimated", "collision": "authored"},
          implements=[{"type": "tube/v1", "programs": {"newton": "tube.newton"},
                       "config": {"robots": []}}])
    table = pbr(trimesh.creation.box(extents=(1.2, .8, .05)), [168, 175, 172, 255], metallic=.6)
    asset("environment/usd-fixture-table", "USD fixture work surface",
          'def Mesh "Asset" (prepend apiSchemas = ["PhysicsCollisionAPI", "PhysicsRigidBodyAPI", '
          '"PhysicsMassAPI"]) {\n bool physics:kinematicEnabled = true\n float physics:mass = 20\n'
          + mesh_usd(table) + '}\n', table, dimensionsM=[1.2, .8, .05],
          physics={"massKg": 20., "massSource": "estimated", "collision": "authored"})
    floor = pbr(trimesh.creation.box(extents=(1.6, 1.2, .025)), [187, 182, 170, 255])
    floor.apply_translation([0, 0, -.09])
    wall = pbr(trimesh.creation.box(extents=(1.6, .025, .7)), [213, 210, 198, 255])
    wall.apply_translation([0, .58, .26])
    room = trimesh.Scene([floor, wall])
    usd = 'def Xform "Asset" {\n'
    for name, part in (("Floor", floor), ("Wall", wall)):
        usd += f' def Mesh "{name}" {{\n' + mesh_usd(part) + ' }\n'
    asset("environment/usd-fixture-room", "USD fixture dressing", usd + '}\n', room,
          dressing=True, dimensionsM=[1.6, 1.2, .7])
    scene = ROOT / "scene"
    scene.mkdir(exist_ok=True)
    scene.joinpath("scene.yaml").write_text(yaml.safe_dump({
        "name": "usd-library-fixture", "title": "USD loading: two tubes and dressing",
        "description": "Small authored fixture for library references, PBR visuals and nonphysical dressing.",
        "sources": ["newton"], "stage": "stage.usda", "simulation": {"solver": "xpbd", "warmupSteps": 1},
    }), encoding="utf-8")
    scene.joinpath("devices.yaml").write_text(yaml.safe_dump({
        "scene": "usd-library-fixture", "entities": [{"match": "/World/Lab/Tubes/*",
            "template": "labware/usd-fixture-tube@1", "config": {"locationSites": [
                "/World/Lab/Stations/SlotA", "/World/Lab/Stations/SlotB"]}}],
    }), encoding="utf-8")
    scene.joinpath("locations.yaml").write_text("locations: {}\n", encoding="utf-8")
    scene.joinpath("stage.usda").write_text('''#usda 1.0
(defaultPrim = "World"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "World" {
 def Xform "Lab" {
  def Mesh "Table" (references = @library:assets/environment/usd-fixture-table/1/asset.usda@</Asset>) {
   double3 xformOp:translate = (0, 0, 0.025)
   uniform token[] xformOpOrder = ["xformOp:translate"]
  }
  def Xform "Tubes" {
   def Cylinder "Tube1" (references = @library:assets/labware/usd-fixture-tube/1/asset.usda@</Asset>) {
    double3 xformOp:translate = (-0.14, 0, 0.11)
    uniform token[] xformOpOrder = ["xformOp:translate"]
   }
   def Cylinder "Tube2" (references = @library:assets/labware/usd-fixture-tube/1/asset.usda@</Asset>) {
    double3 xformOp:translate = (0.14, 0, 0.11)
    uniform token[] xformOpOrder = ["xformOp:translate"]
   }
  }
  def Xform "Stations" {
   def Sphere "SlotA" {
    double radius = 0.003
    double3 xformOp:translate = (-0.14, 0, 0.11)
    uniform token[] xformOpOrder = ["xformOp:translate"]
   }
   def Mesh "SlotB" {
    point3f[] points = [(-0.003, 0, 0), (0.003, 0, 0), (0, 0.003, 0)]
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0, 1, 2]
    uniform token subdivisionScheme = "none"
    double3 xformOp:translate = (0.14, 0, 0.11)
    uniform token[] xformOpOrder = ["xformOp:translate"]
   }
  }
 }
 def Xform "Dressing" {
  def Xform "Room" (references = @library:assets/environment/usd-fixture-room/1/asset.usda@</Asset>) {}
 }
}
''', encoding="utf-8")


if __name__ == "__main__":
    generate()
