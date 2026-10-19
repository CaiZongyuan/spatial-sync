"""Capture a real initial scene outside published packages and managed sessions."""

import argparse
import asyncio
import importlib.metadata
import inspect
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app.recordings import freeze_manifest
from packages.assets.registry import AssetRegistry
from packages.library.previews import (
    CODE_ROOTS,
    DATA_ROOTS,
    DEPENDENCY_DIGEST,
    SCHEMA,
    file_hash,
    input_hash,
)
from packages.library.scenes import BUILTIN_ROOT, SceneLibrary, content_hash
from packages.protocol.schema import Command, SessionOptions
from packages.transport import FrameCodec

PROVENANCE_ENV = "LW_SCENE_PREVIEW_PROVENANCE_FILE"


def capture_runner(*args):
    """Tool-only spawn target; the production Runner/Source remain unchanged."""
    from packages.sim_runtime.runner import run_runner

    files, errors, code_cache = set(), [], {}
    observing = True

    def data_file(value, *, required=False):
        if not isinstance(value, (str, os.PathLike)):
            return
        path = Path(value).resolve()
        if path.is_relative_to(ROOT) and path.relative_to(ROOT).as_posix().startswith(DATA_ROOTS):
            if path.is_file():
                files.add(path)
                return
        if required:
            errors.append(f"Native preview input is not a stable repository file: {value}")

    def opened(event, values):
        if observing and event == "open" and isinstance(values[0], (str, os.PathLike)):
            mode = values[1]
            if not isinstance(mode, str) or "r" in mode:
                data_file(values[0])

    def profiled(frame, event, result):
        filename = frame.f_code.co_filename
        if event == "call":
            if filename not in code_cache:
                path = Path(filename).resolve()
                code_cache[filename] = path if path.is_relative_to(ROOT) and path.relative_to(
                    ROOT
                ).as_posix().startswith(CODE_ROOTS) else None
            if path := code_cache[filename]:
                files.add(path)
        module, name = frame.f_globals.get("__name__"), frame.f_code.co_name
        if module == "newton._src.utils.import_urdf":
            if name == "parse_urdf" and event == "call":
                data_file(frame.f_locals["source"], required=True)
            elif name == "resolve_urdf_asset" and event == "return":
                if result and result[0]:
                    data_file(result[0], required=True)
                elif frame.f_locals.get("filename"):
                    errors.append("Native URDF asset could not be resolved to a stable input")
        elif module == "packages.sim_runtime.loaders" and name == "import_usd" and event == "return":
            stage = frame.f_locals.get("stage")
            if stage:
                from pxr import UsdUtils

                for layer in stage.GetUsedLayers():
                    if not layer.anonymous:
                        data_file(layer.realPath, required=True)
                layers, assets, unresolved = UsdUtils.ComputeAllDependencies(str(frame.f_locals["path"]))
                for layer in layers:
                    if not layer.anonymous:
                        data_file(layer.realPath, required=True)
                for asset in assets:
                    data_file(asset, required=True)
                if unresolved:
                    errors.append(f"Native USD dependencies could not be resolved: {unresolved}")

    sys.addaudithook(opened)
    previous = sys.getprofile()
    sys.setprofile(profiled)
    try:
        run_runner(*args)
    finally:
        sys.setprofile(previous)
        observing = False
        Path(os.environ[PROVENANCE_ENV]).write_text(json.dumps({
            "files": [path.relative_to(ROOT).as_posix() for path in sorted(files)],
            "errors": errors,
        }), encoding="utf-8")


@contextmanager
def native_capture_inputs(enabled):
    if not enabled:
        yield None
        return
    from packages.sim_runtime import runner

    with tempfile.TemporaryDirectory(prefix="lw-scene-preview-") as directory:
        output = Path(directory) / "native-inputs.json"
        with patch.object(runner, "run_runner", capture_runner), patch.dict(
            os.environ, {PROVENANCE_ENV: str(output)}
        ):
            yield output


def module_inputs(*objects):
    """Follow referenced project symbols of the producer, not whole directories."""
    pending = list(objects)
    found, paths = set(), set()
    while pending:
        item = pending.pop()
        if id(item) in found:
            continue
        found.add(id(item))
        if isinstance(item, (staticmethod, classmethod)):
            pending.append(item.__func__)
            continue
        if isinstance(item, property):
            pending.extend(function for function in (item.fget, item.fset) if function)
            continue
        if isinstance(item, dict):
            pending.extend(item.values())
            continue
        if isinstance(item, (list, tuple, set)):
            pending.extend(item)
            continue
        module = item if isinstance(item, ModuleType) else inspect.getmodule(item)
        file = getattr(module, "__file__", None)
        if not file:
            continue
        path = Path(file).resolve()
        if not path.is_relative_to(ROOT):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if not relative.startswith(CODE_ROOTS):
            continue
        paths.add(path)
        if inspect.isfunction(item):
            pending.extend(item.__globals__[name] for name in item.__code__.co_names
                           if name in item.__globals__)
        elif inspect.isclass(item):
            pending.extend(vars(item).values())
            pending.extend(item.__bases__)
        elif isinstance(item, ModuleType):
            pending.extend(value for value in vars(item).values()
                           if callable(value) and inspect.getmodule(value) is item)
    return paths


async def capture(version, source_kind, root):
    if source_kind == "synthetic":
        from apps.api.app.sources.synthetic_source import SyntheticSource as Source
    elif source_kind == "newton":
        from apps.api.app.sources.newton_source import NewtonSource as Source
    else:
        raise ValueError("Unsupported preview source")

    cache_root = Path(root).resolve()
    user_root = Path(os.getenv("LW_LIBRARY", "~/.spatial-sync/library")).expanduser()
    package_roots = (BUILTIN_ROOT / "scenes", user_root / "scenes", version.path.parents[1])
    if any(cache_root.is_relative_to(path.resolve()) for path in package_roots):
        raise ValueError("Derived captures must stay outside published scene packages")
    version.require_launchable()
    if source_kind not in version.metadata["sources"]:
        raise ValueError("Source is not supported by this version")
    opened = set()
    observing = True

    def observe(event, args):
        if not observing or event != "open" or not isinstance(args[0], (str, os.PathLike)):
            return
        mode = args[1]
        if isinstance(mode, str) and "r" not in mode:
            return
        path = Path(args[0]).resolve()
        if (path.is_relative_to(ROOT) and path.suffix != ".pyc"
                and path.relative_to(ROOT).as_posix().startswith((*CODE_ROOTS, *DATA_ROOTS))):
            opened.add(path)

    sys.addaudithook(observe)
    source = Source("preview-" + version.content_hash[:20])
    source.configure_scene(version)
    with native_capture_inputs(source_kind == "newton") as native_inputs:
        try:
            manifest, catalog = await source.start(version.builder, SessionOptions(device="cpu"))
            await source.execute(Command(requestId="capture-pause", command="pause"))
            if source_kind == "newton":
                await source.execute(Command(requestId="capture-initial", command="reset"))
            frame = (await source.snapshot()).model_copy(deep=True)
            state = source.entity_snapshot().model_copy(deep=True)
            registry = AssetRegistry.from_file()
            frozen, assets = await freeze_manifest(manifest, source, registry)
        finally:
            await source.close()
            observing = False
        native = json.loads(native_inputs.read_bytes()) if native_inputs else {"files": [], "errors": []}
        if native["errors"]:
            raise ValueError("; ".join(native["errors"]))
    if content_hash(version.path) != version.content_hash:
        raise ValueError("Scene package changed while capturing")
    dependencies = module_inputs(
        type(version), Source, FrameCodec, freeze_manifest, AssetRegistry, capture, capture_runner,
        native_capture_inputs,
    )
    dependencies.update(path for path in opened if path.is_file())
    dependencies.update(ROOT / name for name in native["files"])
    dependencies.add(ROOT / "uv.lock")
    directory = Path(root) / version.name / str(version.rev) / version.content_hash / source_kind
    directory.mkdir(parents=True, exist_ok=False)
    documents = {"manifest": "manifest.json", "catalog": "catalog.json", "spatial": "snapshot.bin",
                 "state": "state.json"}
    codec = FrameCodec(manifest.sessionId, manifest.sceneRevision, manifest.revisionNumericId)
    payloads = {
        "manifest.json": json.dumps(frozen, separators=(",", ":")).encode(),
        "catalog.json": catalog.model_dump_json().encode(),
        "state.json": state.model_dump_json().encode(),
        "snapshot.bin": codec.encode(frame),
        **assets,
    }
    for name, payload in payloads.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    counts = {
        "physicalEntities": sum(node.kind in ("body", "static")
                                and not node.entityKey.startswith("/World/Dressing/")
                                for node in manifest.nodes),
        "bindings": len(catalog.entities), "groups": sum(node.kind == "group" for node in manifest.nodes),
    }
    value = {
        "schema": SCHEMA, "sceneVersion": version.id, "sceneContentHash": version.content_hash,
        "source": source_kind, "capturedAt": datetime.now(UTC).isoformat(), "simTime": frame.simTime,
        "runtime": {"python": sys.version.split()[0], "source": Source.__name__, "device": "cpu",
                    **{name: importlib.metadata.version(name) for name in (
                        ("numpy", "newton", "warp-lang", "mujoco", "usd-core")
                        if source_kind == "newton" else ("numpy",)
                    )}},
        "dependencyDigest": DEPENDENCY_DIGEST,
        "dependencies": [{"path": path.relative_to(ROOT).as_posix(), "sha256": input_hash(path)}
                         for path in sorted(dependencies)],
        "documents": documents, "files": {name: file_hash(directory / name) for name in payloads},
        "counts": counts, "bounds": None, "thumbnail": {},
    }
    (directory / "capture.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene_version")
    parser.add_argument("--source", choices=["synthetic", "newton"], required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    args = parser.parse_args()
    version = SceneLibrary().resolve(args.scene_version, launch=True, source=args.source)
    value = asyncio.run(capture(version, args.source, args.cache_root))
    print(json.dumps({"sceneVersion": value["sceneVersion"], "sceneContentHash": value["sceneContentHash"],
                      "counts": value["counts"], "dependencies": len(value["dependencies"])}, indent=2))


if __name__ == "__main__":
    main()
