"""Read-only, exact-version scene captures. Importing this module starts no source."""

import hashlib
import re
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

    @field_validator("templateAssets")
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


class PreviewRepository:
    """Serve indexed capture bytes after validating package and actual inputs."""

    def __init__(self, root, *, bundled_root=REPO_ROOT / "assets/scene-previews"):
        self.roots = (Path(root), Path(bundled_root))

    def _capture(self, version, source):
        expected_templates = version.template_dependencies()
        if source not in version.metadata["sources"]:
            raise ValueError("所选物理源不支持此场景版本")
        if content_hash(version.path) != version.content_hash:
            raise ValueError("场景包内容已变化，请发布新版本")
        reason = "此版本尚无匹配的静态预览；可启动后查看实时场景"
        for root in self.roots:
            directory = root / version.name / str(version.rev) / version.content_hash / source
            if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
                continue
            index = relative_file(directory, "capture.json")
            if not index.is_file():
                continue
            try:
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
                dependencies = value["dependencies"]
                if not dependencies or any(input_hash(dependency_path(item)) != item["sha256"]
                                           for item in dependencies):
                    raise ValueError("预览的构建输入已变化，需要重新捕获")
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
                return directory, value
            except (OSError, KeyError, TypeError, ValueError) as exc:
                reason = str(exc)
        raise ValueError(reason)

    def describe(self, version, source):
        try:
            _, capture = self._capture(version, source)
        except (OSError, ValueError) as exc:
            return {"status": "unavailable", "source": source, "reason": str(exc),
                    "entityCount": None, "bindingCount": None}
        base = f"/api/v1/library/scenes/{version.name}/{version.rev}/preview/{source}/"
        return {"status": "available", "source": source, "reason": None,
                "sceneVersion": version.id, "sceneContentHash": version.content_hash,
                "capturedAt": capture["capturedAt"], "simTime": capture["simTime"],
                "entityCount": capture["counts"]["physicalEntities"],
                "bindingCount": capture["counts"]["bindings"],
                "groupCount": capture["counts"]["groups"],
                "bounds": capture.get("bounds"), "runtime": capture["runtime"],
                "documents": {key: base + name for key, name in capture["documents"].items()},
                "thumbnail": {key: base + name for key, name in capture.get("thumbnail", {}).items()}}

    def read(self, version, source, relative):
        directory, capture = self._capture(version, source)
        if relative not in capture["files"]:
            raise ValueError("预览文件不在索引中")
        path = relative_file(directory, relative)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != capture["files"][relative]:
            raise ValueError("预览文件校验失败")
        return payload
