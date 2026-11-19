"""Prepare a robot from confined source files for the existing Asset transaction."""

import hashlib
import json
import shutil
from pathlib import PurePosixPath

import yaml

from packages.library.archive import invalid, package_files, relative_path
from packages.library.robot_import.recipe import Manifest, Recipe


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def read_document(path, schema):
    if path.stat().st_size > 1024 * 1024:
        raise invalid("Robot manifest or recipe exceeds 1 MiB", path=path.name)
    try:
        return schema.model_validate_json(path.read_bytes())
    except ValueError as exc:
        raise invalid(str(exc), path=path.name) from exc


def prepare(root, target, *, recipe_json=None, base=None):
    from apps.node.usd_mjcf import UnsupportedUsdPhysics
    from packages.library.robot_import import bake, urdf
    from packages.library.robot_import.source import Source

    try:
        manifest = read_document(root / "robot.json", Manifest)
        recipe = manifest.calibration if recipe_json is None else Recipe.model_validate_json(recipe_json)
        for value in manifest.packages.values():
            relative_path(value)
        entry = relative_path(manifest.entry).as_posix()
        license_name = relative_path(manifest.license).as_posix()
        source = Source(root, manifest.packages)
        if entry not in source.files:
            raise invalid("Missing robot entry", path=entry)
        if license_name not in source.files or not (root / license_name).stat().st_size:
            raise invalid("Original license notices are required", path=license_name)
        suffix = PurePosixPath(entry).suffix.lower()
        baking_recipe = recipe
        if suffix == ".urdf":
            robot = urdf.load(source, entry)
        elif suffix in (".xml", ".mjcf"):
            from packages.library.robot_import import mjcf

            robot = mjcf.load(source, entry, recipe.standingKey)
        elif suffix in (".usd", ".usda", ".usdc"):
            from packages.library.robot_import import usd

            robot = usd.load(source, entry, recipe.metersPerUnit)
            baking_recipe = recipe.model_copy(update={"metersPerUnit": None})
        else:
            raise invalid("Expected an expanded URDF, MJCF or USD entry", path=entry)
        target.mkdir()
        mapping, estimated, mass = bake.bake(robot, baking_recipe, target)
    except UnsupportedUsdPhysics as exc:
        raise invalid(str(exc), path=exc.prim_path) from exc
    except (ValueError, KeyError, OSError, RuntimeError, TypeError) as exc:
        raise invalid(str(exc), path="robot.json" if recipe_json is None else "calibration") from exc

    shutil.copytree(root, target / "source")
    shutil.copyfile(root / license_name, target / "LICENSE")
    files = {}
    for name in package_files(target / "source"):
        stored = "source/" + name
        path = target / stored
        with path.open("rb") as stream:
            files[stored] = {"sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                             "bytes": path.stat().st_size}
    write_json(target / "provenance.json", {
        "schema": "lw.robot-provenance/1", "entry": "source/" + entry, "licenseId": manifest.licenseId,
        "license": "source/" + license_name, "upstream": manifest.upstream, "revision": manifest.revision,
        "files": files, "estimatedMassPrims": estimated, "calibratedFrom": base,
    })
    write_json(target / "calibration.json", recipe.model_dump(mode="json"))
    write_json(target / "joint-calibration.json", mapping)
    metadata = {
        "id": manifest.id, "kind": "robot", "title": manifest.title,
        "stage": "asset.usda", "visual": "visual.glb",
        "physics": {"collision": "authored", "massKg": mass},
        "simulation": {"solver": "mujoco", "physicsDt": .002},
        "implements": [item.model_dump(exclude_none=True, mode="json") for item in recipe.implements],
    }
    if estimated:
        metadata["physics"]["massSource"] = "estimated"
    (target / "asset.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
