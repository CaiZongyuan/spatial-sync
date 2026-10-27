"""Build and import the FR3 repair from a received fr3-image-lab@3 library.

Uses the public import API and its assigned revisions. Creates new immutable
packages only; never starts, pauses or replaces a Session. Requires USD extras.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml
from pxr import Gf, Usd, UsdGeom, UsdPhysics

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.cli.lw.client import GatewayClient
from packages.library.scenes import SceneLibrary
from tools.repair_fr3_rack import build as build_rack
from tools.repair_fr3_rotor import build as build_rotor
from tools.repair_fr3_tube import build as build_tube


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def replace_reference(path, name, old_revision, new_revision):
    text = path.read_text(encoding="utf-8")
    before = f"{name}/{old_revision}/asset.usda"
    if text.count(before) != 1:
        raise ValueError(f"Expected exactly one received reference to {before} in {path}")
    path.write_text(text.replace(before, f"{name}/{new_revision}/asset.usda"), encoding="utf-8")


async def repair(library, output, url):
    library, output = library.resolve(), output.resolve()
    if output.exists():
        raise ValueError("A fresh output directory is required; receipts are never overwritten")
    output.mkdir(parents=True)
    recipe = json.loads(Path(__file__).with_name("fr3_repair_recipe.json").read_text())
    versions = {}

    async def upload(client, name, directory):
        receipt = await client.library_import(directory)
        write_json(output / (name + "-receipt.json"), receipt)
        version = receipt.get("assetVersion", receipt.get("sceneVersion"))
        versions[name] = version
        write_json(output / "versions.json", versions)
        print(version, receipt["status"], flush=True)
        return version.rsplit("@", 1)[1]

    async with GatewayClient(url=url) as client:
        # Unchanged references retain their immutable IDs, including nested lid
        # and environment assets. Refuse another bundle before publishing any part.
        original = SceneLibrary(user_root=library, state_root=output / "state").resolve("fr3-image-lab@3")
        dependencies = original.asset_dependencies()
        for item in dependencies:
            target = await client.library_describe(item["id"])
            if target["contentHash"] != item["contentHash"]:
                raise ValueError(f"Target dependency differs from received bundle: {item['id']}")
        write_json(output / "dependency-preflight.json", dependencies)
        rotor = output / "rotor"
        build_rotor(library / "assets/device/fr3-lab-rotor-a/1", rotor)
        revision = await upload(client, "rotor", rotor)
        parent = output / "centrifuge"
        shutil.copytree(
            library / "assets/device/fr3-lab-centrifuge-a/1",
            parent,
            ignore=shutil.ignore_patterns("validation.json"),
        )
        replace_reference(parent / "asset.usda", "fr3-lab-rotor-a", "1", revision)
        provenance = json.loads((parent / "provenance.json").read_text(encoding="utf-8"))
        provenance["repair"] = {"issue": 213, "rotorAsset": versions["rotor"]}
        provenance["physicsVerified"] = False
        provenance["files"] = {
            path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
            for path in parent.iterdir()
            if path.name != "provenance.json"
        }
        write_json(parent / "provenance.json", provenance)
        parent_revision = await upload(client, "centrifuge", parent)

        tube_revisions = {}
        for index in range(1, 7):
            name = f"fr3-lab-tube-{index:02d}"
            target = output / name
            build_tube(library / f"assets/labware/{name}/1", target)
            tube_revisions[name] = await upload(client, name, target)
        rack = output / "rack"
        build_rack(library / "assets/labware/fr3-lab-rack/1", rack, recipe["rackSites"])
        rack_revision = await upload(client, "rack", rack)

        scene = output / "scene"
        shutil.copytree(
            library / "scenes/fr3-image-lab/3", scene, ignore=shutil.ignore_patterns("validation.json")
        )
        base = scene / "base.usda"
        replace_reference(base, "fr3-lab-centrifuge-a", "1", parent_revision)
        replace_reference(base, "fr3-lab-rack", "1", rack_revision)
        for name, revision in tube_revisions.items():
            replace_reference(base, name, "1", revision)
        locations_path = scene / "locations.yaml"
        locations = yaml.safe_load(locations_path.read_text(encoding="utf-8"))
        for slot, taught in recipe["rotorLocations"].items():
            locations["locations"]["centrifuge.slot" + slot].update(taught)
        locations_path.write_text(yaml.safe_dump(locations, sort_keys=False), encoding="utf-8")

        # Remove old per-part tube overrides: those 245 children no longer exist.
        gaps = Usd.Stage.Open(str(scene / "contact-gap.usda"))
        for index in range(1, 7):
            spec = gaps.GetRootLayer().GetPrimAtPath(f"/World/Lab/Tubes/Tube{index:02d}")
            for child in list(spec.nameChildren.values()) if spec else []:
                if child.name.startswith("Collision"):
                    gaps.RemovePrim(child.path)
        gaps.GetRootLayer().Save()

        anchor = Usd.Stage.CreateNew(str(scene / "rack-anchor.usda"))
        UsdGeom.SetStageUpAxis(anchor, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(anchor, 1)
        anchor.SetDefaultPrim(UsdGeom.Xform.Define(anchor, "/World").GetPrim())
        UsdGeom.Xform.Define(anchor, "/World/Lab")
        joint = UsdPhysics.FixedJoint.Define(anchor, "/World/Lab/RackWorldAnchor")
        joint.CreateBody1Rel().SetTargets(["/World/Lab/Rack"])
        joint.CreateLocalPos0Attr(Gf.Vec3f(0, 0.35, 0.95))
        joint.CreateLocalRot0Attr(Gf.Quatf(1, Gf.Vec3f(0, 0, 0)))
        joint.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
        joint.CreateLocalRot1Attr(Gf.Quatf(1, Gf.Vec3f(0, 0, 0)))
        UsdPhysics.ArticulationRootAPI.Apply(joint.GetPrim())
        anchor.GetRootLayer().Save()
        root = Usd.Stage.Open(str(scene / "stage.usda"))
        root.GetRootLayer().subLayerPaths.insert(0, "rack-anchor.usda")
        root.GetRootLayer().Save()

        metadata_path = scene / "scene.yaml"
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        metadata["description"] = (
            "FR3 with 130mm rotor seat radius, tapered rotor/rack supports and sealed rigid tube proxies. "
            "Four slots re-taught with actual FK. Estimated physics; weld teaching only."
        )
        metadata["derivedFrom"].update(
            {
                "sourceSceneVersion": "fr3-image-lab@3",
                "rotorSupportRecipe": "tapered-guide-v1",
                "rotorSlotRadiusM": 0.13,
                "tubeCollisionRecipe": "sealed-tube-envelope-v1",
                "rackSupportRecipe": "rack-tapered-inserts-v1",
            }
        )
        metadata["derivedFrom"]["teaching"]["kind"] = "top-down, rotor-tangent jaws; collision-checked FK"
        metadata_path.write_text(
            yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        await upload(client, "scene", scene)
    return versions


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path, help="Received library with original assets@1 and scene@3")
    parser.add_argument("output", type=Path, help="Fresh local authoring/receipt directory")
    parser.add_argument("--url", required=True, help="Gateway whose local library will receive new versions")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(repair(args.library, args.output, args.url)), indent=2))
