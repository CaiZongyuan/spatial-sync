"""Extract small, honest binding-reference packages from existing spatial content.

Run with the base dev environment: ``uv run --no-sync python tools/package_binding_references.py
--output <empty-directory>``. This reads the pure Synthetic builder and a retained CPU Manifest;
it never starts Newton, generates a GLB, supplies a license, or claims physics validation.
Existing package directories are never overwritten. See docs/ASSET_LIBRARY.md for the scope.
"""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import yaml
from pxr import Gf, Usd, UsdGeom

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.protocol.internal import SessionOptions
from packages.sim_runtime.synthetic_lab import build_manifest

SOURCE_REVISION = "882607f56e07464c14e839f2e39832d358c06062"
NATIVE_MANIFEST = (
    "assets/scene-previews/robot-and-tubes/1/"
    "c594aa8010bfaf6a8f93b4213c0f43c2186782c695ab231bf5ce752ad78cec8e/newton/manifest.json"
)
BASE = "/World/Lab/"


def fingerprint(path):
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def write_stage(path, node, sites):
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    for index, visual in enumerate(node["visuals"]):
        geometry = visual["geometry"]
        key = f"/Asset/Visual{index + 1}"
        if geometry["type"] == "box":
            shape = UsdGeom.Cube.Define(stage, key)
            shape.CreateSizeAttr(1)
            shape.AddScaleOp().Set(Gf.Vec3f(*(2 * value for value in geometry["halfExtents"])))
        elif geometry["type"] == "cylinder":
            shape = UsdGeom.Cylinder.Define(stage, key)
            shape.CreateRadiusAttr(geometry["radius"])
            shape.CreateHeightAttr(2 * geometry["halfHeight"])
            shape.CreateAxisAttr("Z")
        elif geometry["type"] == "capsule":
            shape = UsdGeom.Capsule.Define(stage, key)
            shape.CreateRadiusAttr(geometry["radius"])
            shape.CreateHeightAttr(2 * geometry["halfHeight"])
            shape.CreateAxisAttr("Z")
        else:
            raise ValueError("Reference extraction supports only the selected real primitives")
        # Translate/orient precede scale so the source local translation is not rescaled.
        p, q = visual["localPose"]["p"], visual["localPose"]["q"]
        translate = shape.AddTranslateOp()
        translate.Set(Gf.Vec3d(*p))
        orient = shape.AddOrientOp()
        orient.Set(Gf.Quatf(q[3], Gf.Vec3f(*q[:3])))
        shape.SetXformOpOrder([translate, orient, *shape.GetOrderedXformOps()[:-2]])
    for relative, pose in sites.items():
        site = UsdGeom.Xform.Define(stage, "/Asset/" + relative)
        site.AddTranslateOp().Set(Gf.Vec3d(*pose["p"]))
        q = pose["q"]
        site.AddOrientOp().Set(Gf.Quatf(q[3], Gf.Vec3f(*q[:3])))
    stage.GetRootLayer().Export(str(path))
    path.write_bytes(path.read_bytes().rstrip(b"\r\n") + b"\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    package = ROOT / "library/scenes/lab-demo/2"
    source_inputs = [
        "packages/sim_runtime/synthetic_lab.py",
        NATIVE_MANIFEST,
        *(f"library/scenes/lab-demo/2/{name}.yaml" for name in ("scene", "devices", "locations")),
    ]
    for relative in source_inputs:
        frozen = subprocess.check_output(
            ["git", "-c", "core.longpaths=true", "show", f"{SOURCE_REVISION}:{relative}"], cwd=ROOT
        )
        if frozen != (ROOT / relative).read_bytes():
            raise ValueError("Input differs from its pinned source revision: " + relative)
    scene = SimpleNamespace(
        path=package,
        metadata=yaml.safe_load((package / "scene.yaml").read_bytes()),
        bindings=yaml.safe_load((package / "devices.yaml").read_bytes()),
    )
    synthetic = build_manifest("reference-extraction", SessionOptions(device="cpu"), scene)
    synthetic_nodes = {node.entityKey: node.model_dump(mode="json") for node in synthetic.nodes}
    native_nodes = {
        node["entityKey"]: node for node in json.loads((ROOT / NATIVE_MANIFEST).read_bytes())["nodes"]
    }
    references = [
        (
            "robot/synthetic-arm-reference",
            "Synthetic arm hand proxy",
            "robot-arm/v1",
            {"synthetic": "robot_arm.virtual"},
            "RobotA/Hand",
            {},
            {},
        ),
        (
            "device/lab-centrifuge-reference",
            "Laboratory centrifuge housing proxy",
            "centrifuge/v1",
            {"synthetic": "centrifuge.virtual", "newton": "centrifuge.newton"},
            "Centrifuge/Housing",
            {"slotSites": [f"Rotor/Slot{i}" for i in range(1, 5)]},
            {
                f"Rotor/Slot{i}": next(
                    s.localPose.model_dump(mode="json")
                    for s in synthetic.sites
                    if s.entityKey == BASE + f"Centrifuge/Rotor/Slot{i}"
                )
                for i in range(1, 5)
            },
        ),
        (
            "labware/synthetic-tube-reference",
            "Synthetic 80 mm tube proxy",
            "tube/v1",
            {"synthetic": "tube.virtual"},
            "Tubes/Tube01",
            {},
            {},
        ),
        (
            "device/synthetic-incubator-reference",
            "Synthetic incubator box proxy",
            "incubator/v1",
            {"synthetic": "incubator.virtual"},
            "Incubator",
            {},
            {},
        ),
        (
            "device/synthetic-cabinet-reference",
            "Synthetic biosafety cabinet box proxy",
            "biosafety-cabinet/v1",
            {"synthetic": "biosafety_cabinet.virtual"},
            "BiosafetyCabinet",
            {},
            {},
        ),
        (
            "device/synthetic-sensor-reference",
            "Synthetic environment sensor box proxy",
            "env-sensor/v1",
            {"synthetic": "env_sensor.virtual"},
            "EnvironmentSensor",
            {},
            {},
        ),
        (
            "labware/native-small-tube-reference",
            "Native 30 mm tube proxy",
            "tube/v1",
            {"newton": "tube.newton"},
            "Tubes/Tube01",
            {},
            {},
        ),
        (
            "labware/native-capsule-tube-reference",
            "Native capsule tube proxy",
            "tube/v1",
            {"newton": "tube.newton"},
            "Tubes/Tube05",
            {},
            {},
        ),
    ]
    destinations = [output / "assets" / identifier / "1" for identifier, *_ in references]
    if any(path.exists() for path in destinations):
        raise ValueError("Output already contains a reference version; published packages are immutable")
    for (identifier, title, device_type, programs, key, config, sites), destination in zip(
        references, destinations
    ):
        native = identifier.startswith("labware/native-")
        source_path = NATIVE_MANIFEST if native else "packages/sim_runtime/synthetic_lab.py"
        node = (native_nodes if native else synthetic_nodes)[BASE + key]
        destination.mkdir(parents=True)
        (destination / "source").mkdir()
        write_stage(destination / "asset.usda", node, sites)
        metadata = {
            "id": identifier,
            "kind": identifier.split("/")[0],
            "title": title,
            "description": "Binding reference with extracted primitive visuals only. "
            "World placement, articulation, collision, mass, GLB and license are not supplied. "
            "Registered Python scenes retain their existing spatial builders.",
            "stage": "asset.usda",
            "implements": [{"type": device_type, "programs": programs, "config": config}],
        }
        (destination / "asset.yaml").write_text(
            yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8", newline="\n"
        )
        write_json(
            destination / "source/geometry.json",
            {
                "repository": "https://github.com/CaiZongyuan/spatial-sync",
                "sourceRevision": SOURCE_REVISION,
                "source": source_path,
                "sourceFile": fingerprint(ROOT / source_path),
                "entityKey": node["entityKey"],
                "visuals": node["visuals"],
                "sites": sites,
                "normalization": "Copy visual-local poses into /Asset; omit source node/world pose. "
                "Sites retain their original rotor-local poses; the rotor body/articulation is not supplied.",
            },
        )
        write_json(
            destination / "provenance.json",
            {
                "repository": "https://github.com/CaiZongyuan/spatial-sync",
                "sourceRevision": SOURCE_REVISION,
                "producer": {"path": "tools/package_binding_references.py", **fingerprint(Path(__file__))},
                "sourceFiles": {source_path: fingerprint(ROOT / source_path)}
                | (
                    {}
                    if native
                    else {
                        str(p.relative_to(ROOT).as_posix()): fingerprint(p)
                        for p in (
                            package / "scene.yaml",
                            package / "devices.yaml",
                            package / "locations.yaml",
                        )
                    }
                ),
                "toolVersions": {
                    "python": platform.python_version(),
                    **{name: version(name) for name in ("usd-core", "pyyaml", "pydantic")},
                },
                "license": "Not established; repository presence does not grant redistribution permission",
                "limitations": [
                    "Primitive reference, not a complete physical device",
                    "GLB not generated",
                    "No collision, mass or inertia supplied",
                    "Physics validation not run",
                ],
                "files": {
                    p.relative_to(destination).as_posix(): fingerprint(p)
                    for p in sorted(destination.rglob("*"))
                    if p.is_file()
                },
            },
        )
    print(json.dumps({"output": str(output), "assets": len(destinations), "physics": "not run"}))


if __name__ == "__main__":
    main()
