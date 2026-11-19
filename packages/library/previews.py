"""Read-only, exact-version scene captures. Importing this module starts no source."""

import asyncio
import hashlib
import json
import os
import re
import sys
from contextlib import suppress
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    ValidationError,
    field_validator,
    model_validator,
)

from packages.library.read_cache import ReadCache, stamps, tree_paths
from packages.library.scenes import REPO_ROOT, content_hash
from packages.library.templates import template_id
from packages.protocol.schema import Catalog, EntitySnapshot, SceneManifest
from packages.transport import FrameCodec

SCHEMA = "lw.scene-preview/1"
DEPENDENCY_DIGEST = "sha256-text-lf-v1"
CODE_ROOTS = (
    "packages/", "apps/api/app/sources/", "apps/api/app/recordings.py", "tools/capture_scene_preview.py",
)
DATA_ROOTS = ("assets/", "devices/types/", "examples/usd-lab/", "library/")

_NonemptyString = Annotated[str, Field(min_length=1)]
_Vector = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]


class _Bounds(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    min: _Vector
    max: _Vector
    scope: _NonemptyString

    @model_validator(mode="after")
    def ordered(self):
        if any(lower > upper for lower, upper in zip(self.min, self.max, strict=True)):
            raise ValueError("bounds min must not exceed max")
        return self


class _TemplateAsset(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str
    contentHash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @field_validator("id")
    @classmethod
    def exact_identity(cls, value):
        return template_id(value)


class _CaptureMetadata(BaseModel):
    """Validate every field exposed by describe before a cache can be selected."""

    model_config = ConfigDict(strict=True, extra="allow")
    capturedAt: AwareDatetime
    simTime: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    runtime: dict[str, _NonemptyString]
    documents: dict[str, _NonemptyString]
    counts: dict[str, Annotated[int, Field(ge=0)]]
    thumbnail: dict[str, _NonemptyString] = Field(default_factory=dict)
    bounds: _Bounds | None = None
    templateAssets: list[_TemplateAsset] = Field(default_factory=list)
    libraryAssets: list[_TemplateAsset] = Field(default_factory=list)

    @field_validator("templateAssets", "libraryAssets")
    @classmethod
    def unique_templates(cls, value):
        if len({item.id for item in value}) != len(value):
            raise ValueError("Template asset identities must be unique")
        return value

    @field_validator("runtime")
    @classmethod
    def runtime_identity(cls, value):
        if not {"python", "source", "device"}.issubset(value):
            raise ValueError("runtime requires python, source and device")
        return value


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def input_hash(path):
    """Source text has identical input identity in LF and CRLF checkouts."""
    path = Path(path)
    if path.suffix in {".py", ".json", ".yaml", ".yml", ".lock", ".toml", ".urdf", ".xml", ".usda"}:
        return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return file_hash(path)


def relative_file(root, name):
    if not isinstance(name, str) or "\\" in name or PureWindowsPath(name).drive:
        raise ValueError("Invalid capture path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or not relative.parts or any(part in (".", "..") for part in relative.parts):
        raise ValueError("Invalid capture path")
    path = Path(root) / name
    if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Capture file escapes its indexed root")
    return path


def dependency_path(dependency):
    name = dependency["path"]
    if not isinstance(name, str) or not (
        name == "uv.lock" or name.startswith(CODE_ROOTS) or name.startswith(DATA_ROOTS)
    ) or name.startswith("assets/scene-previews/"):
        raise ValueError("Capture dependency is outside supported source inputs")
    return relative_file(REPO_ROOT, name)


def type_inputs(dependencies):
    return {item["path"] for item in dependencies
            if PurePosixPath(item["path"]).parent == PurePosixPath("devices/types")
            and Path(item["path"]).match("*.yaml")}


def inputs_current(dependencies):
    # load_catalog reads the whole glob whenever type files are capture inputs.
    recorded = type_inputs(dependencies)
    if recorded and recorded != {path.relative_to(REPO_ROOT).as_posix()
                                 for path in (REPO_ROOT / "devices/types").glob("*.yaml")}:
        return False
    return all(input_hash(dependency_path(item)) == item["sha256"] for item in dependencies)


class PreviewRepository:
    """Serve indexed capture bytes after validating package and actual inputs."""

    def __init__(self, root, *, bundled_root=REPO_ROOT / "assets/scene-previews"):
        self.roots = (Path(root), Path(bundled_root))
        self.cache = ReadCache()

    def _capture(self, version, source):
        directory, capture, stale = self._capture_with_state(version, source, allow_stale=False)
        if stale:
            raise ValueError("预览的构建输入已变化，需要重新捕获")
        return directory, capture

    def _capture_with_state(self, version, source, *, allow_stale=False):
        key = (version.id, version.content_hash, source, version.template_error, version.asset_error,
               tuple(sorted((item["id"], item["contentHash"]) for item in
                            (*version.template_assets.values(), *version.runtime_assets.values()))))
        cached = self.cache.get(key)
        if cached is not None:
            directory, value, stale = cached
            if stale and not allow_stale:
                raise ValueError("预览的构建输入已变化，需要重新捕获")
            return directory, value, stale
        paths = tree_paths(version.path, version.path.parents[2])
        for root in self.roots:
            directory = root / version.name / str(version.rev) / version.content_hash / source
            paths.update(tree_paths(directory, root))
            paths.add(directory / "error.json")
            try:
                capture = json.loads((directory / "capture.json").read_bytes())
                paths.update(dependency_path(item) for item in capture["dependencies"])
                if type_inputs(capture["dependencies"]):
                    paths.add(REPO_ROOT / "devices/types")
            except (OSError, ValueError, KeyError, TypeError):
                pass  # _inspect retains the precise validation error and source fallback.
        for snapshot in (*version.template_assets.values(), *version.runtime_assets.values()):
            path = Path(snapshot["path"])
            paths.update(tree_paths(path, path.parents[3]))
        paths = tuple(sorted(paths))
        recorded = stamps(paths)
        result = self._inspect(version, source)
        directory, value, stale = self.cache.put(key, result, paths, recorded)
        if stale and not allow_stale:
            raise ValueError("预览的构建输入已变化，需要重新捕获")
        return directory, value, stale

    def _inspect(self, version, source):
        if source not in version.metadata["sources"]:
            raise ValueError("所选物理源不支持此场景版本")
        reason = "此版本尚无匹配的静态预览；可启动后查看实时场景"
        for root in self.roots:
            directory = root / version.name / str(version.rev) / version.content_hash / source
            if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
                continue
            error_file = relative_file(directory, "error.json")
            index = relative_file(directory, "capture.json")
            if not index.is_file():
                if error_file.is_file():
                    try:
                        err = json.loads(error_file.read_bytes())
                        if (
                            err.get("sceneContentHash") == version.content_hash
                            and isinstance(err.get("message"), str)
                            and err["message"].strip()
                        ):
                            reason = err["message"].strip()
                    except (OSError, ValueError, TypeError):
                        pass
                continue
            try:
                expected_templates = version.template_dependencies()
                expected_assets = version.asset_dependencies()
                if content_hash(version.path) != version.content_hash:
                    raise ValueError("场景包内容已变化，请发布新版本")
                try:
                    value = _CaptureMetadata.model_validate_json(index.read_bytes()).model_dump(mode="json")
                except ValidationError as exc:
                    fields = ", ".join(sorted({".".join(map(str, item["loc"])) or "capture"
                                               for item in exc.errors(include_input=False)}))
                    raise ValueError("预览元数据无效：" + fields) from exc
                if (value["schema"], value["sceneVersion"], value["sceneContentHash"], value["source"]) != (
                    SCHEMA, version.id, version.content_hash, source
                ):
                    raise ValueError("预览归属与当前场景版本或物理源不一致")
                if value.get("dependencyDigest") != DEPENDENCY_DIGEST:
                    raise ValueError("预览的构建输入摘要规则不受支持，需要重新采集")
                if {item["id"]: item["contentHash"] for item in value["templateAssets"]} != {
                    item["id"]: item["contentHash"] for item in expected_templates
                }:
                    raise ValueError("Preview template dependencies are incomplete or differ; recapture")
                if {item["id"]: item["contentHash"] for item in value["libraryAssets"]} != {
                    item["id"]: item["contentHash"] for item in expected_assets
                }:
                    raise ValueError("Preview USD asset dependencies are incomplete or differ; recapture")
                dependencies = value["dependencies"]
                if not dependencies:
                    raise ValueError("预览的构建输入已变化，需要重新捕获")
                stale = not inputs_current(dependencies)
                files = value["files"]
                if not isinstance(files, dict) or not files:
                    raise ValueError("预览文件索引为空")
                for name, digest in files.items():
                    if file_hash(relative_file(directory, name)) != digest:
                        raise ValueError("预览文件校验失败")
                for document in ("manifest", "catalog", "spatial", "state"):
                    if document not in value["documents"]:
                        raise ValueError("预览缺少必要的快照文件")
                references = (*value["documents"].values(), *value["thumbnail"].values())
                if any(name not in files for name in references):
                    raise ValueError("预览文档或缩略图未列入文件索引")
                documents = value["documents"]
                manifest = SceneManifest.model_validate_json((directory / documents["manifest"]).read_bytes())
                for node in manifest.nodes:
                    if node.kind == "group":
                        continue
                    for visual in (*node.visuals, *(node.fallbackVisuals or []), *node.colliders):
                        if visual.kind == "primitive":
                            continue
                        if visual.url not in files and not re.fullmatch(
                            r"assets/missing-[a-f0-9]{64}\.(?:glb|gltf)", visual.url
                        ):
                            raise ValueError("预览模型引用了未索引的资产")
                catalog = Catalog.model_validate_json((directory / documents["catalog"]).read_bytes())
                state = EntitySnapshot.model_validate_json((directory / documents["state"]).read_bytes())
                codec = FrameCodec(manifest.sessionId, manifest.sceneRevision, manifest.revisionNumericId)
                frame = codec.decode(
                    (directory / documents["spatial"]).read_bytes()
                )
                bodies = {node.streamId for node in manifest.nodes if node.kind == "body"}
                if (manifest.source.kind != source or catalog.sceneRevision != manifest.sceneRevision
                        or state.sceneRevision != manifest.sceneRevision or state.epoch != frame.epoch
                        or {row[0] for row in frame.poses} != bodies):
                    raise ValueError("预览快照身份或空间实体集合不一致")
                if value["simTime"] != frame.simTime:
                    raise ValueError("预览时间与实际空间快照不一致")
                counts = {
                    "physicalEntities": sum(node.kind in ("body", "static")
                                            and not node.entityKey.startswith("/World/Dressing/")
                                            for node in manifest.nodes),
                    "bindings": len(catalog.entities),
                    "groups": sum(node.kind == "group" for node in manifest.nodes),
                }
                if value["counts"] != counts:
                    raise ValueError("预览统计与真实快照不一致")
                return directory, value, stale
            except (OSError, KeyError, TypeError, ValueError) as exc:
                reason = str(exc)
        raise ValueError(reason)

    def describe(self, version, source):
        try:
            _, capture, stale = self._capture_with_state(version, source, allow_stale=True)
        except (OSError, ValueError) as exc:
            return {"status": "unavailable", "source": source, "reason": str(exc),
                    "entityCount": None, "bindingCount": None}
        base = f"/api/v1/library/scenes/{version.name}/{version.rev}/preview/{source}/"
        return {"status": "stale" if stale else "available", "source": source,
                "reason": "预览的构建输入已变化，当前为旧版采集预览；建议重新采集" if stale else None,
                "sceneVersion": version.id, "sceneContentHash": version.content_hash,
                "capturedAt": capture["capturedAt"], "simTime": capture["simTime"],
                "entityCount": capture["counts"]["physicalEntities"],
                "bindingCount": capture["counts"]["bindings"],
                "groupCount": capture["counts"]["groups"],
                "bounds": capture.get("bounds"), "runtime": capture["runtime"],
                "documents": {key: base + name for key, name in capture["documents"].items()},
                "thumbnail": {key: base + name for key, name in capture.get("thumbnail", {}).items()}}

    def read(self, version, source, relative, *, allow_stale=True):
        directory, capture, stale = self._capture_with_state(version, source, allow_stale=allow_stale)
        # Cache metadata and decoding, never authorization to serve altered source/package bytes.
        version.template_dependencies()
        version.asset_dependencies()
        if content_hash(version.path) != version.content_hash or (not allow_stale and stale):
            raise ValueError("预览的构建输入已变化，需要重新捕获")
        if relative not in capture["files"]:
            raise ValueError("预览文件不在索引中")
        path = relative_file(directory, relative)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != capture["files"][relative]:
            raise ValueError("预览文件校验失败")
        return payload


class ScenePreviewCapturer:
    """Run tools/capture_scene_preview.py out-of-process for newly imported or stale scenes."""

    def __init__(self, previews: PreviewRepository, state_root: Path, user_root: Path):
        self.previews = previews
        self.state_root = Path(state_root)
        self.user_root = Path(user_root)
        self._tasks: dict[tuple[str, str, str], asyncio.Task] = {}
        self._procs: set[asyncio.subprocess.Process] = set()

    def _write_error(self, version, source: str, message: str):
        directory = self.previews.roots[0] / version.name / str(version.rev) / version.content_hash / source
        if (directory / "capture.json").is_file():
            return
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "error.json").write_text(
            json.dumps(
                {"sceneContentHash": version.content_hash, "source": source, "message": message},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.previews.cache.clear()

    def _clear_error(self, version, source: str):
        error_file = (
            self.previews.roots[0]
            / version.name
            / str(version.rev)
            / version.content_hash
            / source
            / "error.json"
        )
        error_file.unlink(missing_ok=True)
        self.previews.cache.clear()

    def schedule(self, version, source: str):
        key = (version.id, version.content_hash, source)
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(self._run_safe(version, source))
        self._tasks[key] = task
        task.add_done_callback(lambda _done: self._tasks.pop(key, None))
        return task

    async def _run_safe(self, version, source: str):
        with suppress(asyncio.CancelledError, OSError, ValueError, RuntimeError):
            await self.capture_now(version, source, overwrite=False)

    async def capture_now(self, version, source: str, *, overwrite: bool = True):
        if source not in version.metadata["sources"]:
            raise ValueError("所选物理源不支持此场景版本")
        version.require_launchable()
        target = self.previews.roots[0] / version.name / str(version.rev) / version.content_hash / source
        if not overwrite and (target / "capture.json").is_file():
            return
        script = REPO_ROOT / "tools/capture_scene_preview.py"
        env = os.environ.copy()
        env["LW_LIBRARY"] = str(self.user_root)
        env["SPATIAL_STATE_ROOT"] = str(self.state_root)
        env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        command = [
            sys.executable,
            str(script),
            version.id,
            "--source",
            source,
            "--cache-root",
            str(self.previews.roots[0]),
        ]
        if not overwrite:
            command.append("--no-overwrite")
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._procs.add(proc)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            message = "静态预览采集超时"
            self._write_error(version, source, message)
            raise RuntimeError(message) from None
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                with suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            raise
        finally:
            self._procs.discard(proc)
        if proc.returncode != 0:
            raw = stderr.decode("utf-8", errors="replace").strip().splitlines()
            detail = raw[-1] if raw else f"exit {proc.returncode}"
            message = (
                "自动采集静态预览失败（当前环境可能缺少对应物理源依赖）：" + detail[:240]
            )
            self._write_error(version, source, message)
            raise RuntimeError(message)
        self._clear_error(version, source)

    async def close(self):
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for proc in list(self._procs):
            if proc.returncode is None:
                proc.terminate()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
