"""Build and run the local Blender-to-platform appearance experiment (#214).

    uv run --no-sync python tools/realism_sample.py demo --blender <blender.exe>

Only this command's API/Web processes and Session are closed on Ctrl+C.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import trimesh
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lw.client import GatewayClient, GatewayError

from factory.procedural import rack_mesh_json, rack_package, tube_package
from packages.library.visuals import glb_budget

RACK = {
    "widthM": .17, "depthM": .10, "heightM": .06, "wallThicknessM": .005,
    "baseThicknessM": .006, "plateThicknessM": .008, "holeDiameterM": .020,
    "tubeDiameterM": .016, "clearanceM": .002, "pitchXM": .045, "pitchYM": .035,
    "rows": 2, "columns": 3, "collision": "holes", "massDensityKgM3": 700.,
}
TUBE = {"diameterM": .016, "heightM": .10, "massDensityKgM3": 1200.}
HOME = [0., -.4, 0., -2., 0., 1.6, .7]
KINDS = {"bench": "environment", "rack": "environment", "tube": "labware",
         "room": "environment", "instrument": "device"}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def stage():
    result = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(result, "/Asset")
    result.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(result, "Z")
    UsdGeom.SetStageMetersPerUnit(result, 1.)
    return result, root.GetPrim()


def collision_box(result, path, center, size):
    cube = UsdGeom.Cube.Define(result, path)
    cube.CreateSizeAttr(1.)
    cube.AddTranslateOp().Set(Gf.Vec3d(*center))
    cube.AddScaleOp().Set(Gf.Vec3f(*size))
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    cube.CreatePurposeAttr("guide")


def contact_material(stage):
    # Numerical contact compliance for the centimetre-scale authored sample,
    # not a measurement of glass/polymer material properties.
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            prim.CreateAttribute("newton:contact_ke", Sdf.ValueTypeNames.Float).Set(1000.)
            prim.CreateAttribute("newton:contact_kd", Sdf.ValueTypeNames.Float).Set(10.)


def build(args):
    request = {"rackMesh": rack_mesh_json(RACK), "rack": RACK, "tube": TUBE}
    script = ROOT / "factory/realism_sample_blender.py"
    digest = hashlib.sha256(script.read_bytes() + json.dumps(request, sort_keys=True).encode()).hexdigest()
    output = args.workspace / "builds" / digest[:16]
    output.mkdir(parents=True, exist_ok=True)
    request_path = output / "request.json"
    write_json(request_path, request)
    if not (output / "appearance-build.json").exists():
        blender = Path(args.blender).resolve() if args.blender else (
            ROOT.parents[1] / ".scratch/tools/blender/blender-4.5.9-windows-x64/blender.exe"
        )
        if not blender.is_file():
            raise ValueError("Supply --blender pointing to Blender 4.5.9 LTS")
        with (output / "blender.log").open("w", encoding="utf-8") as log:
            subprocess.run([str(blender), "--background", "--factory-startup", "--threads", "8",
                            "--python-exit-code", "1",
                            "--python", str(script), "--", "--request", str(request_path),
                            "--output", str(output)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                           check=True, env=os.environ | {"DISABLE_TELEMETRY": "true"})
    receipt = json.loads((output / "appearance-build.json").read_text(encoding="utf-8"))
    for filename, expected in receipt["files"].items():
        artifact = output / filename
        actual = hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.parent == output else None
        if actual != expected["sha256"]:
            raise ValueError("Cached appearance changed; use a fresh workspace to rebuild: " + filename)
    packages = output / "packages"
    packages.mkdir(exist_ok=True)
    for name, kind in KINDS.items():
        package = packages / name
        package.mkdir(exist_ok=True)
        if name in ("rack", "tube"):
            spec = {"id": kind + "/realism-" + name, "version": 1, "seed": 214,
                    "kind": kind, "license": "CC0-1.0", name: RACK if name == "rack" else TUBE}
            files, _ = (rack_package if name == "rack" else tube_package)(spec)
            for filename, value in files.items():
                (package / filename).write_bytes(value)
        else:
            usd, root = stage()
            if name == "bench":
                UsdPhysics.RigidBodyAPI.Apply(root).CreateKinematicEnabledAttr(True)
                mass = UsdPhysics.MassAPI.Apply(root)
                mass.CreateMassAttr(50.)
                mass.CreateCenterOfMassAttr(Gf.Vec3f(0, 0, .4))
                mass.CreateDiagonalInertiaAttr(Gf.Vec3f(5., 18., 18.))
                collision_box(usd, "/Asset/Top", (0, 0, .80), (1.90, .90, .056))
                for index, (x, y) in enumerate(((-.82, -.34), (-.82, .34), (.82, -.34), (.82, .34))):
                    collision_box(usd, "/Asset/Leg" + str(index), (x, y, .385), (.048, .048, .77))
            usd.GetRootLayer().Export(str(package / "asset.usda"))
        shutil.copyfile(output / (name + ".glb"), package / "visual.glb")
        metadata = {
            "id": kind + "/realism-" + name, "kind": kind,
            "title": {"bench": "Stainless sample preparation bench", "rack": "Six-hole tube rack",
                      "tube": "Simplified capped sample tube", "room": "Laboratory room dressing",
                      "instrument": "Generic centrifuge appearance study"}[name],
            "description": "Local appearance experiment; authored design, not a measured device replica.",
            "stage": "asset.usda", "visual": "visual.glb", "dressing": name in ("room", "instrument"),
        }
        if name == "bench":
            metadata["physics"] = {"massKg": 50., "massSource": "estimated", "collision": "authored"}
        elif name in ("rack", "tube"):
            previous = yaml.safe_load((package / "asset.yaml").read_text(encoding="utf-8"))
            metadata["physics"] = previous["physics"]
        usd = Usd.Stage.Open(str(package / "asset.usda"))
        if name == "tube":
            # A rounded tube uses a capsule proxy with the same outer bounds.
            # Flat cylinder/box contacts were unstable in the MuJoCo sample;
            # the original failed versions remain in the local library.
            prim = usd.GetPrimAtPath("/Asset/Glass")
            prim.SetTypeName("Capsule")
            UsdGeom.Capsule(prim).GetHeightAttr().Set(.084)
            radius, height = .008, .1
            mass = 1200 * (math.pi * radius ** 2 * (height - 2 * radius)
                           + 4 / 3 * math.pi * radius ** 3)
            mass_api = UsdPhysics.MassAPI(usd.GetPrimAtPath("/Asset"))
            mass_api.GetMassAttr().Set(mass)
            mass_api.GetDiagonalInertiaAttr().Set(Gf.Vec3f(
                mass * (3 * radius ** 2 + height ** 2) / 12,
                mass * (3 * radius ** 2 + height ** 2) / 12, mass * radius ** 2 / 2))
            metadata["physics"]["massKg"] = mass
        contact_material(usd)
        usd.GetRootLayer().Save()
        bounds = trimesh.load_scene(package / "visual.glb", process=False).bounds
        metadata["dimensionsM"] = (bounds[1] - bounds[0]).tolist()
        (package / "asset.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
        (package / "LICENSE").write_text("CC0-1.0\nOriginal procedural appearance authored for Lab World.\n",
                                        encoding="utf-8")
        write_json(package / "provenance.json", {
            "recipe": "realism-sample/1", "inputHash": digest,
            "packageBuilderSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "blender": receipt["blender"], "device": receipt["device"],
            "visual": receipt["files"][name + ".glb"],
            "bake": receipt["bakeStats"] if name == "instrument" else None,
            "physics": "Canonical rack/tube recipe; bench authored estimates; dressing has no physics",
            "contact": {"stiffnessNPerM": 1000., "dampingNsPerM": 10.,
                        "basis": "Numerical compliance for the authored sample; not measured"},
            "approximations": ["Tube collision is a capsule, mass uses nominal density, "
                               "inertia uses a bounding cylinder, glass/liquid rendering is simplified"]
            if name == "tube" else [],
            "gaps": ["Generic instrument is Dressing: no opening, spinning or device behavior"]
            if name == "instrument" else [],
            "files": {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "bytes": path.stat().st_size}
                      for path in package.iterdir() if path.is_file() and path.name != "provenance.json"},
        })
        glb_budget(package / "visual.glb")
    write_json(args.workspace / "build.json", {"directory": str(output), "inputHash": digest,
                                               "packages": {name: str(packages / name) for name in KINDS}})
    print("Built appearance packages: " + str(packages), flush=True)
    return packages


def compose(packages, versions):
    result = Usd.Stage.CreateInMemory()
    world = UsdGeom.Xform.Define(result, "/World")
    result.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(result, "Z")
    UsdGeom.SetStageMetersPerUnit(result, 1.)
    UsdGeom.Xform.Define(result, "/World/Lab")
    UsdGeom.Xform.Define(result, "/World/Dressing")
    references = []

    def reference(path, identifier, position, filename="asset.usda"):
        bare, revision = identifier.split("@")
        obj = UsdGeom.Xform.Define(result, path)
        references.append((path, f"library:assets/{bare}/{revision}/{filename}"))
        obj.AddTranslateOp().Set(Gf.Vec3d(*position))
        return obj

    reference("/World/Lab/Bench", versions["bench"], (0, 0, 0))
    reference("/World/Lab/Rack", versions["rack"], (.20, -.20, .828))
    reference("/World/Lab/Robot", "robot/fr3@1", (-.48, .03, .828), "asset.usdc")
    for index, value in enumerate(HOME, 1):
        joint = result.OverridePrim("/World/Lab/Robot/fr3_joint" + str(index))
        joint.CreateAttribute("newton:angular:position", Sdf.ValueTypeNames.Double).Set(math.degrees(value))
        drive = UsdPhysics.DriveAPI.Apply(joint, "angular")
        drive.CreateTypeAttr("force")
        drive.CreateTargetPositionAttr(math.degrees(value))
        drive.CreateStiffnessAttr(24000. * math.pi / 180)
        drive.CreateDampingAttr(240. * math.pi / 180)
    for index in (1, 2):
        joint = result.OverridePrim("/World/Lab/Robot/fr3_finger_joint" + str(index))
        joint.CreateAttribute("newton:linear:position", Sdf.ValueTypeNames.Double).Set(.035)
        drive = UsdPhysics.DriveAPI.Apply(joint, "linear")
        drive.CreateTargetPositionAttr(.035)
        drive.CreateStiffnessAttr(500.)
        drive.CreateDampingAttr(20.)
    for row in range(2):
        for column in range(3):
            tube = reference(f"/World/Lab/Tubes/Tube{row * 3 + column + 1:02}", versions["tube"],
                             (.20 + (column - 1) * .045, -.20 + (row - .5) * .035, .834))
            # The first appearance study fixes tubes in their holders. Free-body
            # versions remain recorded separately; they failed the long-run probe.
            UsdPhysics.RigidBodyAPI.Apply(tube.GetPrim()).CreateKinematicEnabledAttr(True)
    reference("/World/Dressing/Room", versions["room"], (0, 0, 0))
    reference("/World/Dressing/Instrument", versions["instrument"], (.56, .12, .828))
    collision_box(result, "/World/Lab/Floor", (0, 0, -.026), (3., 2., .05))
    contact_material(result)
    scene = packages.parent / "scene"
    scene.mkdir(exist_ok=True)
    result.GetRootLayer().Export(str(scene / "stage.usda"))
    # Author portable library references in a detached layer. Only the gateway
    # resolves them; attempting live USD composition here would emit false gaps.
    layer = Sdf.Layer.FindOrOpen(str(scene / "stage.usda"))
    for path, filename in references:
        layer.GetPrimAtPath(path).referenceList.prependedItems = [Sdf.Reference(filename, "/Asset")]
    layer.Save()
    documents = {
        "scene.yaml": {"name": "realism-workbench-display", "title": "Lab World · Baked-material workbench",
                       "description": "Baked PBR housing, steel bench, fixed tubes and interactive FR3. "
                       "Appearance study: tubes are fixed in the rack; instrument is visual-only.",
                       "stage": "stage.usda", "sources": ["newton"],
                       "simulation": {"solver": "mujoco", "warmupSteps": 1}},
        "devices.yaml": {"scene": "realism-workbench-display", "entities": [
            {"key": "/World/Lab/Robot", "template": "robot/fr3@1",
             "config": {"locations": "locations.yaml", "tubes": []}}]},
        "locations.yaml": {"robot": "/World/Lab/Robot", "locations": {}},
    }
    for filename, value in documents.items():
        (scene / filename).write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    write_json(scene / "factory.json", {"recipe": "realism-sample/1", "assets": versions,
                                       "robot": "robot/fr3@1", "gaps": ["Instrument is Dressing",
                                       "Tubes are fixed display instances; free-body long-run remains failed",
                                       "FR3 retains native per-body geometry fallback"]})
    return scene


async def install(args):
    built = json.loads((args.workspace / "build.json").read_text(encoding="utf-8"))
    versions, receipts = {}, {}
    async with GatewayClient(args.url) as client:
        for name, package in built["packages"].items():
            receipt = await client.library_import(package)
            receipts[name] = receipt
            write_json(args.workspace / "imports.json", receipts)
            versions[name] = receipt["assetVersion"]
            if any(row["status"] == "fail" for row in receipt["validation"]["checks"]):
                raise RuntimeError("Imported asset failed validation: " + receipt["assetVersion"])
            print("Imported " + receipt["assetVersion"], flush=True)
        scene = compose(Path(built["directory"]) / "packages", versions)
        receipt = await client.library_import(scene)
        receipts["scene"] = receipt
        write_json(args.workspace / "imports.json", receipts)
        failed = [row for row in receipt["validation"]["checks"] if row["status"] == "fail"]
        if failed:
            raise RuntimeError("Scene validation failed: " + json.dumps(failed, ensure_ascii=False))
        print("Validated scene " + receipt["sceneVersion"], flush=True)
        return receipt["sceneVersion"]


def available(port):
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", port))


def demo(args):
    build(args)
    available(args.api_port)
    available(args.web_port)
    args.url = f"http://127.0.0.1:{args.api_port}"
    runtime = args.workspace / "runtime"
    runtime.mkdir(exist_ok=True)
    env = os.environ | {"LW_LIBRARY": str(runtime / "library"), "SPATIAL_STATE_ROOT": str(runtime / "state"),
                        "SPATIAL_RECORDINGS_ROOT": str(runtime / "recordings"),
                        "SPATIAL_SCENE_PREVIEWS_ROOT": str(runtime / "previews"),
                        "SPATIAL_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.web_port}",
                        "SPATIAL_API_URL": args.url, "SPATIAL_NEWTON_DEVICE": "cpu"}
    processes, logs, session = [], [], None
    try:
        for command, name in (
            ([sys.executable, "-m", "uvicorn", "apps.api.app.main:app", "--host", "127.0.0.1",
              "--port", str(args.api_port)], "api"),
            ([shutil.which("node"), str(ROOT / "apps/web/node_modules/vite/bin/vite.js"),
              str(ROOT / "apps/web"), "--host", "127.0.0.1", "--port", str(args.web_port)], "web"),
        ):
            log = (runtime / (name + ".log")).open("w", encoding="utf-8")
            logs.append(log)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                               stderr=subprocess.STDOUT, creationflags=flags))
        for _ in range(120):
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("Owned service stopped; inspect runtime logs")
            try:
                response = httpx.get(args.url + "/api/v1/health", timeout=1, trust_env=False)
                response.raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(.5)
        else:
            raise RuntimeError("Owned API did not become ready")
        identifier = asyncio.run(install(args))
        with (runtime / "preview.log").open("w", encoding="utf-8") as log:
            subprocess.run([sys.executable, str(ROOT / "tools/capture_scene_preview.py"), identifier,
                            "--source", "newton", "--cache-root", str(runtime / "previews")],
                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)

        async def launch():
            async with GatewayClient(args.url) as client:
                return await client.scene_launch(identifier, source="newton")

        session = asyncio.run(launch())
        write_json(args.workspace / "session.json", session)
        url = f"http://127.0.0.1:{args.web_port}/sessions/{session['sessionId']}"
        print("View the scene at " + url, flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(.5)
    finally:
        if session:
            try:
                httpx.delete(args.url + "/api/v1/sessions/" + session["sessionId"],
                             timeout=30, trust_env=False).raise_for_status()
            except httpx.HTTPError as error:
                print("Owned Session cleanup: " + str(error), file=sys.stderr)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "install", "demo"))
    parser.add_argument("--workspace", type=Path, default=ROOT / ".scratch/realism-sample")
    parser.add_argument("--blender")
    parser.add_argument("--url", default="http://127.0.0.1:8074")
    parser.add_argument("--api-port", type=int, default=8074)
    parser.add_argument("--web-port", type=int, default=5274)
    options = parser.parse_args()
    options.workspace = options.workspace.resolve()
    options.workspace.mkdir(parents=True, exist_ok=True)
    try:
        if options.action == "build":
            build(options)
        elif options.action == "install":
            asyncio.run(install(options))
        else:
            demo(options)
    except KeyboardInterrupt:
        pass
    except GatewayError as error:
        print(json.dumps(error.document(), ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from error
