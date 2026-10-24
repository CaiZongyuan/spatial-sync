"""Restricted preparation of the pinned SimReady source packages.

This module proves the SF0 support boundary. It is not a general USD converter:
only the declared package family, Newton physics variant and UsdPreviewSurface
fallback are accepted. Unsupported material or dependency paths become explicit
gaps instead of silent conversions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import trimesh
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, UsdUtils
from trimesh.visual import TextureVisuals
from trimesh.visual.material import PBRMaterial

from packages.acquisition.cache import CACHE_MARKER

DATASET = "nvidia/simready-assets"
REVISION = "524747d9b5e1f21b5b18b56041788c01d745f1a1"
RECIPE = "simready-newton-usdpreview-v1"
ALLOWED_LICENSES = {"CC0-1.0", "CC-BY-4.0"}
STAGE_UP = "Z"
USABLE_STRUCTURE = (1, 2, 4, 6)
STATIC_PHYSICS = ("P1", "P3")
LFS_POINTER_MARKER = "version https://git-lfs.github.com/spec/v1"
# Root-level notices are outside the BOM; they are retained next to the license.
SOURCE_NOTICE_CANDIDATES = ("NOTICE", "NOTICE.txt", ".metadata/NOTICE", ".metadata/NOTICE.txt")


@dataclass(frozen=True)
class PinnedSource:
    package: str
    asset_id: str
    title: str
    description: str
    expected_files: int
    expected_bytes: int
    role: str


SOURCE_PACKAGES = (
    PinnedSource(
        "container_recycle_square_blue_a02",
        "environment/recycle-container",
        "Blue square recycling container",
        "SimReady recycling container prepared from the pinned NVIDIA source.",
        35,
        8482999,
        "dressing and static collision prop",
    ),
    PinnedSource(
        "bucket_utility_gray_a01",
        "environment/utility-bucket",
        "Gray utility bucket",
        "SimReady utility bucket prepared from the pinned NVIDIA source.",
        50,
        15683159,
        "dressing and static collision prop",
    ),
    PinnedSource(
        "box_cardboard_a01",
        "environment/cardboard-box",
        "Cardboard box",
        "SimReady cardboard box prepared from the pinned NVIDIA source.",
        36,
        21530693,
        "dressing and static collision prop",
    ),
    PinnedSource(
        "equipment_utility_cart_e01",
        "environment/utility-cart",
        "Utility equipment cart",
        "SimReady equipment cart prepared from the pinned NVIDIA source.",
        64,
        171038176,
        "dressing and static collision prop",
    ),
    PinnedSource(
        "desk_a01",
        "environment/desk",
        "Laboratory desk",
        "SimReady desk prepared from the pinned NVIDIA source.",
        46,
        242383218,
        "dressing and static collision prop",
    ),
)
SOURCE_PACKAGE_BY_NAME = {candidate.package: candidate for candidate in SOURCE_PACKAGES}


class SourceError(Exception):
    """The source package cannot be used under the SF0 support boundary."""


class AdaptationError(SourceError):
    """The source package was read but cannot be prepared by this recipe."""


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_url(package):
    return f"https://huggingface.co/datasets/{DATASET}/tree/{REVISION}/{package}"


def tree_url(package):
    return f"https://huggingface.co/api/datasets/{DATASET}/tree/{REVISION}/{package}?recursive=true"


def file_url(package, relative):
    return f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{package}/{relative}"


def _read_url(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": "spatial-sync-scene-factory-sf0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_tree(package, *, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            return json.loads(_read_url(tree_url(package), timeout=60))
        except (OSError, ValueError) as exc:
            last = exc
            time.sleep(1 + attempt)
    raise SourceError(f"Cannot read the pinned source tree for {package}: {last}") from last


def fetch_file(package, relative, destination, *, attempts=4):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A per-process suffix keeps two workers on the same raw cache entry from
    # interleaving their partial files; the final replace stays atomic.
    temporary = destination.with_name(f"{destination.name}.part-{os.getpid()}")
    request = urllib.request.Request(
        file_url(package, relative), headers={"User-Agent": "spatial-sync-scene-factory-sf0"}
    )
    last = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
            temporary.replace(destination)
            return destination
        except (OSError, ValueError) as exc:
            last = exc
            temporary.unlink(missing_ok=True)
            time.sleep(2 + attempt * 2)
    raise SourceError(f"Cannot download {package}/{relative}: {last}") from last


def download_package(
    package,
    cache_root,
    *,
    include_thumbnails=True,
    progress=None,
    max_bytes=None,
):
    """Download the pinned package tree and return its local path.

    ``progress(completed_bytes, completed_files, total_bytes, total_files)`` is
    called once before the first file and after every finished file. Reused
    cache files count as completed. ``max_bytes`` bounds the declared tree
    before any transfer and the downloaded bytes afterwards. Both options are
    additive; existing callers keep their behavior.
    """
    entries = [entry for entry in fetch_tree(package) if entry["type"] == "file"]
    selected = [
        entry
        for entry in entries
        if include_thumbnails
        or not (
            entry["path"].removeprefix(package + "/").startswith("simready_usd/.thumbs/")
            or entry["path"].endswith("_thumbnail.png")
        )
    ]
    if not selected:
        raise SourceError(f"Pinned source tree has no files for {package}")
    total_bytes = sum(int(entry.get("size") or 0) for entry in selected)
    total_files = len(selected)
    if max_bytes is not None and total_bytes > max_bytes:
        raise SourceError(
            f"Pinned source tree for {package} declares {total_bytes} bytes, "
            f"above the {max_bytes} byte source limit"
        )
    root = Path(cache_root) / package
    root.mkdir(parents=True, exist_ok=True)
    completed_bytes = 0
    completed_files = 0
    if progress is not None:
        progress(completed_bytes, completed_files, total_bytes, total_files)
    for entry in selected:
        relative = entry["path"].removeprefix(package + "/")
        if not _safe_relative(relative):
            raise SourceError(f"Pinned source tree contains an unsafe path: {relative!r}")
        destination = root / relative
        if destination.is_file() and destination.stat().st_size == entry.get("size", -1):
            completed_bytes += destination.stat().st_size
            completed_files += 1
            if progress is not None:
                progress(completed_bytes, completed_files, total_bytes, total_files)
            continue
        fetch_file(package, relative, destination)
        completed_bytes += destination.stat().st_size
        completed_files += 1
        if max_bytes is not None and completed_bytes > max_bytes:
            raise SourceError(
                f"Downloaded {completed_bytes} bytes for {package}, above the {max_bytes} byte source limit"
            )
        if progress is not None:
            progress(completed_bytes, completed_files, total_bytes, total_files)
    (Path(cache_root) / f"{package}.tree.json").write_text(
        json.dumps({"dataset": DATASET, "revision": REVISION, "package": package, "entries": entries},
                   indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def lfs_pointer_files(source_dir):
    """Return package-relative paths whose content is a Git LFS pointer."""
    source_dir = Path(source_dir)
    marker = LFS_POINTER_MARKER.encode("utf-8")
    found = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.stat().st_size > 1024:
            continue
        with path.open("rb") as stream:
            head = stream.read(len(marker))
        if head == marker:
            found.append(path.relative_to(source_dir).as_posix())
    return found


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _package_files(source_dir):
    source_dir = Path(source_dir)
    return sorted(
        path.relative_to(source_dir).as_posix()
        for path in source_dir.rglob("*")
        if path.is_file() and path.relative_to(source_dir).as_posix() != CACHE_MARKER
    )


def _bom_report(source_dir, bom):
    source_dir = Path(source_dir)
    missing, mismatched, verified = [], [], []
    items = bom.get("items")
    if not isinstance(items, list) or not items:
        raise SourceError("BOM has no items")
    for item in items:
        relative = item.get("relative_path")
        if not isinstance(relative, str) or not _safe_relative(relative):
            raise SourceError(f"BOM contains an unsafe path: {relative!r}")
        path = source_dir / relative
        if not path.is_file():
            missing.append(relative)
            continue
        expected = (item.get("hash") or {}).get("sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise SourceError(f"BOM has no usable sha256 for {relative}")
        if path.stat().st_size != item.get("size") or sha256_file(path) != expected:
            mismatched.append(relative)
            continue
        verified.append(relative)
    known = {item.get("relative_path") for item in items}
    extra = [relative for relative in _package_files(source_dir) if relative not in known]
    return {
        "items": len(items),
        "verified": len(verified),
        "missing": missing,
        "mismatched": mismatched,
        "notInBom": extra,
    }


def _safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and ":" not in value


def _mdl_imports(source_dir):
    source_dir = Path(source_dir)
    files, external, license_evidence = [], [], []
    for path in sorted(source_dir.rglob("*.mdl")):
        relative = path.relative_to(source_dir).as_posix()
        files.append(relative)
        text = path.read_text(encoding="utf-8", errors="replace")
        header = text[:4096]
        if "NVIDIA Corporation" in header and "Redistributions of source code" in header:
            license_evidence.append(
                {"file": relative, "license": "BSD-3-Clause (NVIDIA header)", "evidence": "header"}
            )
        else:
            license_evidence.append({"file": relative, "license": "unknown", "evidence": "header"})
        for line in text.splitlines():
            statement = line.strip()
            if not statement.startswith("import "):
                continue
            target = statement.removeprefix("import ").split(";", 1)[0].strip()
            if target.startswith("::"):
                external.append({"file": relative, "import": target})
    return {"files": files, "externalImports": external, "licenseEvidence": license_evidence}


def _upstream_validation(stage):
    metadata = (stage.GetRootLayer().customLayerData or {}).get("SimReady_Metadata") or {}
    validation = metadata.get("validation") or {}
    features = validation.get("validated_features") or {}
    latest = max(features, default=None)
    records = features.get(latest, {}) if latest else {}
    failing = sorted(
        name for name, value in records.items()
        if isinstance(value, dict) and not value.get("passed", False)
    )
    return {
        "profile": validation.get("profile"),
        "profileVersion": validation.get("profile_version"),
        "date": latest,
        "failingFeatures": failing,
        "features": records,
        "assetLocation": metadata.get("asset_location"),
        "declaredBy": "upstream SimReady layer customLayerData",
    }


def _root_usds(source_dir):
    metadata = Path(source_dir) / ".metadata/com.nvidia.simready.root_usds.json"
    if not metadata.is_file():
        raise SourceError("Missing .metadata/com.nvidia.simready.root_usds.json")
    entries = _load_json(metadata).get("entries")
    if not isinstance(entries, list) or len(entries) != 1:
        raise SourceError("Expected exactly one declared root USD")
    relative = entries[0]
    if not _safe_relative(relative) or not relative.startswith("simready_usd/"):
        raise SourceError(f"Root USD is not a safe package path: {relative!r}")
    path = Path(source_dir) / relative
    if not path.is_file():
        raise SourceError(f"Declared root USD is missing: {relative}")
    return relative, path


def inspect_source(candidate, source_dir):
    """Verify the immutable source facts SF0 relies on; no adaptation yet."""
    source_dir = Path(source_dir)
    package = candidate.package
    manifest_path = source_dir / "com.nvidia.simready.packaging.json"
    bom_path = source_dir / ".metadata/com.nvidia.simready.packaging.bom.json"
    license_path = source_dir / ".metadata/LICENSE.txt"
    for path in (manifest_path, bom_path, license_path):
        if not path.is_file():
            raise SourceError(f"Missing required source metadata: {path.name}")
    pointers = lfs_pointer_files(source_dir)
    if pointers:
        raise SourceError(
            "Source package still contains Git LFS pointer content: " + ", ".join(pointers[:5])
        )
    manifest = _load_json(manifest_path)
    license_name = manifest.get("license")
    if license_name not in ALLOWED_LICENSES:
        raise SourceError(f"Unsupported package license: {license_name!r}")
    bom = _load_json(bom_path)
    license_in_bom = any(
        item.get("relative_path") == ".metadata/LICENSE.txt" for item in bom.get("items", [])
    )
    obligations = (
        ["attribution", "retain license and notices", "mark modifications"]
        if license_name == "CC-BY-4.0"
        else []
    )
    root_relative, root_path = _root_usds(source_dir)
    stage = Usd.Stage.Open(str(root_path))
    if stage is None:
        raise SourceError(f"Cannot open root USD: {root_relative}")
    source_files = _package_files(source_dir)
    report = {
        "package": package,
        "localAssetId": candidate.asset_id,
        "source": source_url(package),
        "revision": REVISION,
        "files": len(source_files),
        "bytes": sum((source_dir / name).stat().st_size for name in source_files),
        "expectedFiles": candidate.expected_files,
        "expectedBytes": candidate.expected_bytes,
        "license": {
            "name": license_name,
            "file": ".metadata/LICENSE.txt",
            "sha256": sha256_file(license_path),
            "bytes": license_path.stat().st_size,
            "manifestSha256": sha256_file(manifest_path),
            "inBom": license_in_bom,
            "attribution": f"NVIDIA SimReady asset {package}",
            "obligations": obligations,
        },
        "bom": _bom_report(source_dir, bom)
        | {"sha256": sha256_file(bom_path), "bytes": bom_path.stat().st_size},
        "rootUsd": root_relative,
        "notices": [
            relative
            for relative in SOURCE_NOTICE_CANDIDATES
            if (source_dir / relative).is_file() and not (source_dir / relative).is_symlink()
        ],
        "mdl": _mdl_imports(source_dir),
        "upstreamValidation": _upstream_validation(stage),
        "up": UsdGeom.GetStageUpAxis(stage),
        "metersPerUnit": UsdGeom.GetStageMetersPerUnit(stage),
    }
    problems = []
    if report["up"] != STAGE_UP or report["metersPerUnit"] != 1:
        problems.append("Source stage is not Z-up with metersPerUnit=1")
    if report["bom"]["missing"] or report["bom"]["mismatched"]:
        problems.append("BOM files are missing or changed")
    if report["files"] != candidate.expected_files or report["bytes"] != candidate.expected_bytes:
        problems.append("Pinned file count or byte total differs from the support record")
    report["problems"] = problems
    report["status"] = "fail" if problems else "pass"
    return report


def _select_newton(stage, *, prim_path="/RootNode"):
    prim = stage.GetPrimAtPath(prim_path)
    if not prim:
        raise AdaptationError(f"Expected variant root {prim_path}")
    sets = prim.GetVariantSets()
    names = sets.GetNames()
    if "Newton" not in names:
        raise AdaptationError("Newton physics variant is missing")
    newton = sets.GetVariantSet("Newton")
    if "Enabled" not in newton.GetVariantNames():
        raise AdaptationError("Newton variant has no Enabled selection")
    newton.SetVariantSelection("Enabled")
    for other in ("MuJoCo", "PhysX"):
        if other in names:
            variant = sets.GetVariantSet(other)
            if "Disabled" in variant.GetVariantNames():
                variant.SetVariantSelection("Disabled")
    return {"selected": "Newton/Enabled", "variantSets": sorted(names)}


def _uninstance(stage):
    count = 0
    for prim in stage.Traverse():
        if prim.IsInstance():
            prim.SetInstanceable(False)
            count += 1
    return count


def _material_for(prim):
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    return material if material else None


def _preview_surface(material):
    for prim in Usd.PrimRange(material.GetPrim()):
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        if shader.GetIdAttr().Get() == "UsdPreviewSurface":
            return shader
    return None


def _shader_input(shader, name):
    attribute = shader.GetInput(name)
    return attribute if attribute is not None and attribute.GetPrim().IsValid() else None


def _input_value(shader, name):
    attribute = _shader_input(shader, name)
    if attribute is None:
        return None, None
    source = attribute.GetConnectedSource()
    if source is not None:
        return None, source
    return attribute.Get(), None


def _source_shader(source):
    if source is None:
        return None
    prim = source[0].GetPrim() if hasattr(source[0], "GetPrim") else source[0]
    return UsdShade.Shader(prim) if prim.IsA(UsdShade.Shader) else None


def _shader_layer_dir(shader):
    attribute = "inputs:file"
    for spec in shader.GetPrim().GetPrimStack():
        layer = spec.layer
        if layer is None:
            continue
        if layer.GetAttributeAtPath(spec.path.AppendProperty(attribute)) is not None:
            identifier = layer.realPath or layer.identifier
            return Path(identifier).parent
    return None


def _resolve_texture(source_dir, value, layer_dir=None):
    raw = value.path if isinstance(value, Sdf.AssetPath) else str(value)
    if "<UDIM>" in raw or "<UVTILE>" in raw or re.search(r"\.\d{4}\.", raw):
        raise AdaptationError(
            "UDIM/UVTILE texture sets are not supported by the restricted GLB path: " + raw
        )
    path = Path(raw)
    if path.is_absolute():
        candidates = [path]
    else:
        candidates = [Path(source_dir) / path]
        if layer_dir is not None:
            candidates.append(Path(layer_dir) / path)
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(Path(source_dir).resolve()):
            continue
        if resolved.is_file():
            return resolved
    raise AdaptationError(f"Texture is missing or escapes the source package: {raw}")


def _copy_texture(path, output_dir, texture_names, approximations):
    path = Path(path).resolve()
    relative = f"textures/{path.name}"
    if relative in texture_names and texture_names[relative] != str(path):
        relative = f"textures/{sha256_file(path)[:8]}-{path.name}"
    if relative in texture_names:
        return relative
    destination = Path(output_dir) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
        if max(width, height) > 4096:
            image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            image.save(destination)
            approximations.append(
                f"Resampled {path.name} from {width}x{height} to {image.width}x{image.height}"
            )
        else:
            shutil.copyfile(path, destination)
    texture_names[relative] = str(path)
    return relative


def _texture_material(
    source_dir, material, output_dir, texture_names, texture_map, approximations, gaps, image_cache
):
    surface = _preview_surface(material)
    if surface is None:
        gaps.append(f"Material {material.GetPath()} has no UsdPreviewSurface fallback")
        return PBRMaterial(name=str(material.GetPath()), baseColorFactor=[0.6, 0.6, 0.6, 1],
                           metallicFactor=0, roughnessFactor=0.8), []
    used = []

    def image_for(shader, file_value):
        if not isinstance(file_value, (str, Sdf.AssetPath)):
            raise AdaptationError("UsdUVTexture has no file asset")
        path = _resolve_texture(source_dir, file_value, _shader_layer_dir(shader))
        relative = _copy_texture(path, output_dir, texture_names, approximations)
        texture_map[str(path)] = relative
        texture_map[path.as_posix()] = relative
        used.append(relative)
        if relative not in image_cache:
            from PIL import Image

            image_cache[relative] = Image.open(Path(output_dir) / relative)
        return image_cache[relative]

    base_value, base_connected = _input_value(surface, "diffuseColor")
    base_texture = None
    base_factor = [0.8, 0.8, 0.8, 1.0]
    if base_connected is not None:
        shader = _source_shader(base_connected)
        if shader is None or shader.GetIdAttr().Get() != "UsdUVTexture":
            gaps.append(f"Unsupported diffuseColor source in {material.GetPath()}")
        else:
            file_attribute = _shader_input(shader, "file")
            file_value = file_attribute.Get() if file_attribute else None
            if file_value:
                base_texture = image_for(shader, file_value)
                color_space = _shader_input(shader, "sourceColorSpace")
                if color_space and color_space.Get() not in ("sRGB", "auto", None):
                    gaps.append(f"Albedo texture is not sRGB in {material.GetPath()}")
            scale = _shader_input(shader, "scale")
            if scale and list(scale.Get() or (1, 1, 1)) != [1, 1, 1]:
                approximations.append(f"Albedo scale ignored in {material.GetPath()}")
    elif base_value is not None:
        base_factor = list(base_value) + [1.0] if len(base_value) == 3 else list(base_value)

    metallic_value, metallic_connected = _input_value(surface, "metallic")
    roughness_value, roughness_connected = _input_value(surface, "roughness")
    metallic_factor = float(metallic_value) if metallic_value is not None else 0.0
    roughness_factor = float(roughness_value) if roughness_value is not None else 0.5
    metallic_roughness_texture = None
    occlusion_texture = None
    if metallic_connected is not None or roughness_connected is not None:
        metallic_shader = _source_shader(metallic_connected) if metallic_connected else None
        roughness_shader = _source_shader(roughness_connected) if roughness_connected else None
        same = (
            metallic_shader is not None
            and roughness_shader is not None
            and metallic_shader.GetPath() == roughness_shader.GetPath()
        )
        metallic_output = metallic_connected[1] if metallic_connected else None
        roughness_output = roughness_connected[1] if roughness_connected else None
        if (
            same
            and metallic_shader.GetIdAttr().Get() == "UsdUVTexture"
            and metallic_output == "b"
            and roughness_output == "g"
        ):
            file_attribute = _shader_input(metallic_shader, "file")
            file_value = file_attribute.Get() if file_attribute else None
            if file_value:
                metallic_roughness_texture = image_for(metallic_shader, file_value)
                occlusion_texture = metallic_roughness_texture
            metallic_factor = 1.0
            roughness_factor = 1.0
        else:
            gaps.append(f"Unsupported metallic/roughness split in {material.GetPath()}")
            metallic_factor = float(metallic_value) if metallic_value is not None else 0.0
            roughness_factor = float(roughness_value) if roughness_value is not None else 0.5

    normal_texture = None
    normal_value, normal_connected = _input_value(surface, "normal")
    if normal_connected is not None:
        shader = _source_shader(normal_connected)
        if shader is None or shader.GetIdAttr().Get() != "UsdUVTexture":
            gaps.append(f"Unsupported normal source in {material.GetPath()}")
        else:
            file_attribute = _shader_input(shader, "file")
            file_value = file_attribute.Get() if file_attribute else None
            if file_value:
                normal_texture = image_for(shader, file_value)
                color_space = _shader_input(shader, "sourceColorSpace")
                if color_space and color_space.Get() not in ("raw", "auto", None):
                    gaps.append(f"Normal texture is not raw/linear in {material.GetPath()}")
    elif normal_value is not None:
        gaps.append(f"Constant normal input is not represented in GLB for {material.GetPath()}")

    opacity_value, _ = _input_value(surface, "opacity")
    opacity = float(opacity_value) if opacity_value is not None else 1.0
    material_value = PBRMaterial(
        name=str(material.GetPath()),
        baseColorTexture=base_texture,
        baseColorFactor=base_factor,
        metallicFactor=metallic_factor,
        roughnessFactor=roughness_factor,
        metallicRoughnessTexture=metallic_roughness_texture,
        normalTexture=normal_texture,
        occlusionTexture=occlusion_texture,
        alphaMode="BLEND" if opacity < 1 else "OPAQUE",
    )
    return material_value, used


def _mesh_groups(prim):
    mesh = UsdGeom.Mesh(prim)
    groups = []
    subsets = UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim))
    material_subsets = [
        subset for subset in subsets
        if subset.GetFamilyNameAttr().Get() == "materialBind"
    ]
    if material_subsets:
        counts = mesh.GetFaceVertexCountsAttr().Get() or []
        faces = list(range(len(counts)))
        covered = set()
        for subset in material_subsets:
            indices = list(subset.GetIndicesAttr().Get() or [])
            covered.update(indices)
            if indices:
                groups.append((subset.GetPrim(), indices))
        remaining = [index for index in faces if index not in covered]
        if remaining:
            groups.append((prim, remaining))
    else:
        groups.append((prim, list(range(len(mesh.GetFaceVertexCountsAttr().Get() or [])))))
    return groups


def _primvar(prim, name):
    api = UsdGeom.PrimvarsAPI(prim)
    value = api.GetPrimvar(name)
    if not value or not value.HasValue():
        return None, None, None
    return list(value.Get()), list(value.GetIndices() or []), value.GetInterpolation()


def _world(prim):
    xformable = UsdGeom.Xformable(prim)
    return xformable.ComputeLocalToWorldTransform(Usd.TimeCode.Default())


def _mesh_geometry(stage, prim, face_indices, material):
    mesh = UsdGeom.Mesh(prim)
    counts = list(mesh.GetFaceVertexCountsAttr().Get() or [])
    indices = list(mesh.GetFaceVertexIndicesAttr().Get() or [])
    points = list(mesh.GetPointsAttr().Get() or [])
    if not points or not counts or not indices:
        return None
    if sum(counts) != len(indices):
        raise AdaptationError(f"Mesh face counts do not match indices: {prim.GetPath()}")
    transform = _world(prim)
    normals, normal_indices, normal_interpolation = _primvar(prim, "normals")
    uvs, uv_indices, uv_interpolation = _primvar(prim, "st")
    polygons, polygon_uvs, polygon_normals = [], [], []
    corner = 0
    for face_index, count in enumerate(counts):
        polygon = indices[corner:corner + count]
        corner += count
        if face_index not in face_indices:
            continue
        uv = []
        if uvs is not None:
            if uv_interpolation == "faceVarying":
                for offset in range(count):
                    index = corner - count + offset
                    uv.append(uvs[uv_indices[index] if uv_indices else index])
            elif uv_interpolation in ("vertex", "varying"):
                for point in polygon:
                    uv.append(uvs[uv_indices[point] if uv_indices else point])
            elif uv_interpolation == "uniform":
                uv.append(uvs[uv_indices[face_index] if uv_indices else face_index])
            else:
                uv.append(uvs[uv_indices[0] if uv_indices else 0])
        normal = []
        if normals is not None:
            if normal_interpolation == "faceVarying":
                for offset in range(count):
                    normal.append(normals[normal_indices[corner - count + offset]] if normal_indices
                                  else normals[corner - count + offset])
            elif normal_interpolation in ("vertex", "varying"):
                for point in polygon:
                    normal.append(normals[normal_indices[point] if normal_indices else point])
            elif normal_interpolation == "uniform":
                normal.append(normals[normal_indices[face_index] if normal_indices else face_index])
            else:
                normal.append(normals[normal_indices[0] if normal_indices else 0])
        polygons.append(polygon)
        polygon_uvs.append(uv or None)
        polygon_normals.append(normal or None)
    triangles, triangle_uvs, triangle_normals = [], [], []
    for polygon, uv, normal in zip(polygons, polygon_uvs, polygon_normals, strict=True):
        for offset in range(1, len(polygon) - 1):
            corners = [0, offset, offset + 1]
            triangle = [polygon[index] for index in corners]
            triangles.append([int(index) for index in triangle])
            triangle_uvs.append([uv[index] for index in corners] if uv else None)
            triangle_normals.append([normal[index] for index in corners] if normal else None)
    if not triangles:
        return None
    world = []
    for triangle in triangles:
        for index in triangle:
            world.append(transform.Transform(Gf.Vec3d(*points[index])))
    if triangle_uvs and all(value is not None for value in triangle_uvs):
        corners = [value for triangle in triangle_uvs for value in triangle]
        texture_uvs = [[float(component) for component in value] for value in corners]
    else:
        texture_uvs = None
    if triangle_normals and all(value is not None for value in triangle_normals):
        rotation = transform.ExtractRotationMatrix()
        corners = [value for triangle in triangle_normals for value in triangle]
        vertex_normals = [
            rotation.TransformDir(Gf.Vec3d(*value)).GetNormalized() for value in corners
        ]
    else:
        vertex_normals = None
    return {
        "vertices": [[float(value) for value in point] for point in world],
        "faces": triangles,
        "uv": texture_uvs,
        "normals": (
            [[float(value) for value in normal] for normal in vertex_normals]
            if vertex_normals
            else None
        ),
        "material": material,
    }


def _physics_summary(stage):
    collision = False
    masses = []
    inertias = False
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Gprim) and prim.HasAPI(UsdPhysics.CollisionAPI):
            collision = True
        if prim.HasAPI(UsdPhysics.MassAPI):
            mass = UsdPhysics.MassAPI(prim).GetMassAttr().Get()
            if mass is not None:
                masses.append(float(mass))
            if UsdPhysics.MassAPI(prim).GetDiagonalInertiaAttr().HasAuthoredValueOpinion():
                inertias = True
    return {
        "collision": collision,
        "massKg": sum(masses) if masses else None,
        "massIsUpstreamDeclaration": bool(masses),
        "inertiaAuthored": inertias,
    }


def _collect_geometry(
    stage, source_dir, output_dir, texture_names, texture_map, approximations, gaps, image_cache
):
    geometries, skipped, materials = [], [], {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        purpose = UsdGeom.Imageable(prim).GetPurposeAttr().Get()
        if purpose in ("guide", "proxy"):
            skipped.append({"path": str(prim.GetPath()), "reason": f"{purpose} purpose"})
            continue
        for owner, face_indices in _mesh_groups(prim):
            material = _material_for(owner) or _material_for(prim)
            if material is None:
                skipped.append({"path": str(owner.GetPath()), "reason": "no bound material"})
                continue
            key = str(material.GetPath())
            if key not in materials:
                prepared, used = _texture_material(
                    source_dir,
                    material,
                    output_dir,
                    texture_names,
                    texture_map,
                    approximations,
                    gaps,
                    image_cache,
                )
                materials[key] = (prepared, used)
            geometry = _mesh_geometry(stage, prim, face_indices, materials[key][0])
            if geometry is not None:
                geometries.append(geometry)
    return geometries, skipped, materials


def _bounds(geometries):
    points = [point for geometry in geometries for point in geometry["vertices"]]
    if not points:
        return None
    minimum = [min(point[index] for point in points) for index in range(3)]
    maximum = [max(point[index] for point in points) for index in range(3)]
    return minimum, maximum


def _layer_paths(layer):
    paths = []
    layer.Traverse(Sdf.Path.absoluteRootPath, paths.append)
    return paths


def _rewrite_asset_paths(layer, rewrite):
    """Rewrite every asset-valued attribute; ModifyAssetPaths skips custom shader inputs."""
    for path in _layer_paths(layer):
        spec = layer.GetObjectAtPath(path)
        if not isinstance(spec, Sdf.AttributeSpec) or not spec.HasDefaultValue():
            continue
        if spec.typeName == Sdf.ValueTypeNames.Asset:
            value = spec.default
            raw = value.path if isinstance(value, Sdf.AssetPath) else str(value)
            spec.default = Sdf.AssetPath(rewrite(raw))
        elif spec.typeName == Sdf.ValueTypeNames.AssetArray:
            values = spec.default or []
            spec.default = [
                Sdf.AssetPath(rewrite(value.path if isinstance(value, Sdf.AssetPath) else str(value)))
                for value in values
            ]


def _unsafe_assets(layer):
    unsafe = []
    for path in _layer_paths(layer):
        spec = layer.GetObjectAtPath(path)
        if not isinstance(spec, Sdf.AttributeSpec) or not spec.HasDefaultValue():
            continue
        if spec.typeName not in (Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray):
            continue
        values = spec.default if spec.typeName == Sdf.ValueTypeNames.AssetArray else [spec.default]
        for value in values or []:
            raw = value.path if isinstance(value, Sdf.AssetPath) else str(value)
            if raw and not _safe_relative(raw):
                unsafe.append({"path": str(path), "value": raw})
    return unsafe


def _write_canonical_stage(stage, output_dir, texture_map):
    flat = stage.Flatten()

    def rewrite(value):
        if value in texture_map:
            return texture_map[value]
        if not value:
            return value
        return "" if not _safe_relative(value) else value

    UsdUtils.ModifyAssetPaths(flat, rewrite)
    root = stage.GetDefaultPrim().GetPath()
    destination = Path(output_dir) / "asset.usda"
    layer = Sdf.Layer.CreateNew(str(destination))
    target = Usd.Stage.Open(layer)
    target.SetMetadata("upAxis", STAGE_UP)
    target.SetMetadata("metersPerUnit", 1.0)
    spec = Sdf.CreatePrimInLayer(layer, "/Asset")
    spec.specifier = Sdf.SpecifierDef
    spec.typeName = "Xform"
    target.SetDefaultPrim(target.GetPrimAtPath("/Asset"))
    Sdf.CopySpec(flat, root, layer, Sdf.Path("/Asset"))
    _rewrite_asset_paths(layer, rewrite)
    layer.documentation = "Canonical asset prepared by the spatial-sync Scene Factory."
    layer.Save()
    unsafe = _unsafe_assets(layer)
    if unsafe:
        raise AdaptationError("Canonical USD still has host asset paths: " + json.dumps(unsafe[:5]))
    check = Usd.Stage.Open(str(destination))
    if check.GetDefaultPrim().GetPath() != Sdf.Path("/Asset"):
        raise AdaptationError("Canonical USD default prim is not /Asset")
    if UsdGeom.GetStageUpAxis(check) != STAGE_UP or UsdGeom.GetStageMetersPerUnit(check) != 1:
        raise AdaptationError("Canonical USD is not Z-up with metersPerUnit=1")
    unresolved = []
    for prim in check.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        attribute = prim.GetAttribute("inputs:file")
        if attribute and attribute.HasAuthoredValue() and not attribute.Get():
            unresolved.append(str(prim.GetPath()))
    if unresolved:
        raise AdaptationError(
            "Canonical USD has unresolved texture references: " + ", ".join(unresolved)
        )
    return flat


def _write_glb(geometries, destination):
    import numpy as np

    scene = trimesh.Scene()
    for index, geometry in enumerate(geometries):
        vertices = np.asarray(geometry["vertices"], dtype=np.float64)
        faces = np.asarray(geometry["faces"], dtype=np.int64)
        uv = np.asarray(geometry["uv"] if geometry["uv"] else [[0.0, 0.0]] * len(vertices), dtype=np.float64)
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        mesh.visual = TextureVisuals(uv=uv, material=geometry["material"])
        scene.add_geometry(mesh, node_name=f"mesh_{index:04d}", geom_name=f"mesh_{index:04d}")
    destination.write_bytes(scene.export(file_type="glb"))


def _preview(source_dir, output_dir):
    source_dir = Path(source_dir)
    candidates = sorted((source_dir / "simready_usd/.thumbs/256x256").glob("*.usd.png"))
    if not candidates:
        candidates = sorted((source_dir / "simready_usd/.thumbs").glob("*_thumbnail.png"))
    if not candidates:
        return None
    destination = Path(output_dir) / "previews/thumbnail.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidates[0], destination)
    return "previews/thumbnail.png"


def _notice(candidate, license_name):
    return (
        f"NVIDIA SimReady asset: {candidate.package}\n"
        f"Source: {source_url(candidate.package)}\n"
        f"Revision: {REVISION}\n"
        f"License: {license_name} (see LICENSE.txt)\n\n"
        "Modifications: selected the Newton physics variant, disabled the other physics variants, "
        "flattened the composition under /Asset, derived visual.glb from UsdPreviewSurface materials, "
        "and copied referenced textures. The package keeps the original license text.\n\n"
        "This is a modified copy. Upstream validation statements are recorded in provenance.json and "
        "are not platform validation.\n"
    )


def _retain_source_notice(source_dir, output_dir):
    """Copy a source NOTICE outside the BOM next to the generated NOTICE."""
    source_dir = Path(source_dir)
    for relative in SOURCE_NOTICE_CANDIDATES:
        path = source_dir / relative
        if not path.is_file() or path.is_symlink():
            continue
        destination = Path(output_dir) / "SOURCE-NOTICE"
        shutil.copyfile(path, destination)
        return {
            "file": "SOURCE-NOTICE",
            "source": relative,
            "sha256": sha256_file(destination),
            "bytes": destination.stat().st_size,
            "note": "Retained source notice; the generated NOTICE is separate and not overwritten.",
        }
    return None


def _write_provenance(output_dir, candidate, source, derivation):
    output_dir = Path(output_dir)
    files = {}
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "provenance.json":
            continue
        relative = path.relative_to(output_dir).as_posix()
        files[relative] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    provenance = {
        "source": source["source"],
        "sourceRevision": source["revision"],
        "sourcePackage": candidate.package,
        "sourceRootUsd": source["rootUsd"],
        "license": source["license"],
        "sourceFiles": {
            item["relative_path"]: item["hash"]["sha256"]
            for item in source["bomItems"]
            if (Path(source["sourceDir"]) / item["relative_path"]).is_file()
        },
        "derivation": derivation,
        "files": files,
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _tool_versions():
    import platform
    from importlib.metadata import PackageNotFoundError, version

    values = {"python": platform.python_version()}
    for name in ("usd-core", "trimesh", "pillow"):
        try:
            values[name] = version(name)
        except PackageNotFoundError:
            values[name] = None
    return values


def adapt(candidate, source_dir, output_dir):
    """Prepare one pinned source into an immutable canonical asset package."""
    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    source = inspect_source(candidate, source_dir)
    source["sourceDir"] = str(Path(source_dir).resolve())
    source["bomItems"] = _load_json(
        Path(source_dir) / ".metadata/com.nvidia.simready.packaging.bom.json"
    )["items"]
    report = {
        "package": candidate.package,
        "localAssetId": candidate.asset_id,
        "revision": REVISION,
        "source": source,
        "status": "fail",
        "gaps": [],
        "approximations": [],
    }
    if source["status"] != "pass":
        report["gaps"].extend(source["problems"])
        return report
    source_stage = Usd.Stage.Open(str(Path(source_dir) / source["rootUsd"]))
    if source_stage is None:
        raise AdaptationError(f"Cannot open {source['rootUsd']}")
    source_stage.SetEditTarget(source_stage.GetSessionLayer())
    try:
        report["variant"] = _select_newton(source_stage)
    except AdaptationError as exc:
        report["gaps"].append(str(exc))
        report["status"] = "gap"
        return report
    report["uninstanced"] = _uninstance(source_stage)
    gaps, approximations = report["gaps"], report["approximations"]
    texture_names, texture_map, image_cache = {}, {}, {}
    try:
        geometries, skipped, materials = _collect_geometry(
            source_stage,
            source_dir,
            output_dir,
            texture_names,
            texture_map,
            approximations,
            gaps,
            image_cache,
        )
    except AdaptationError as exc:
        report["gaps"].append(str(exc))
        report["status"] = "gap"
        return report
    report["skippedGeometry"] = skipped
    if not geometries:
        report["gaps"].append("No visual meshes with supported UsdPreviewSurface materials")
        return report
    physics = _physics_summary(source_stage)
    report["sourcePhysics"] = physics
    bounds = _bounds(geometries)
    dimensions = (
        [round(maximum - minimum, 6) for minimum, maximum in zip(*bounds, strict=True)]
        if bounds
        else None
    )
    _write_canonical_stage(source_stage, output_dir, texture_map)
    visual = output_dir / "visual.glb"
    try:
        _write_glb(geometries, visual)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        report["gaps"].append(f"GLB derivation failed: {exc}")
        return report
    from packages.library.visuals import glb_budget

    try:
        triangles = glb_budget(visual, {"path": str(output_dir), "files": _package_files(output_dir)})
    except ValueError as exc:
        report["gaps"].append(str(exc))
        return report
    collision = physics["collision"]
    mass = physics["massKg"]
    if not collision:
        report["gaps"].append("No authored collision geometry; dressing only")
    if mass is None:
        report["gaps"].append("No finite upstream mass; physics use is not supported")
    metadata = {
        "id": candidate.asset_id,
        "kind": candidate.asset_id.split("/", 1)[0],
        "title": candidate.title,
        "description": candidate.description
        + " Prepared from NVIDIA SimReady; upstream declarations are not platform validation.",
        "stage": "asset.usda",
        "visual": "visual.glb",
        "dimensionsM": dimensions,
        "dressing": not collision,
    }
    if collision:
        metadata["physics"] = {"collision": "authored"}
        if mass is not None:
            metadata["physics"] = {"massKg": mass, "massSource": "estimated", "collision": "authored"}
    (output_dir / "asset.yaml").write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    shutil.copyfile(Path(source_dir) / ".metadata/LICENSE.txt", output_dir / "LICENSE.txt")
    (output_dir / "NOTICE").write_text(
        _notice(candidate, source["license"]["name"]), encoding="utf-8"
    )
    report["sourceNotice"] = _retain_source_notice(source_dir, output_dir)
    preview = _preview(source_dir, output_dir)
    report["output"] = {
        "preview": preview,
        "textures": sorted(texture_names),
        "bytes": sum(path.stat().st_size for path in output_dir.rglob("*") if path.is_file()),
        "files": len(_package_files(output_dir)),
    }
    report["visual"] = {"triangles": triangles, "meshes": len(geometries), "materials": len(materials)}
    report["dimensionsM"] = dimensions
    report["physics"] = metadata.get("physics")
    report["approximations"].append("Selected Newton variant; other physics variants are disabled.")
    report["approximations"].append(
        "GLB uses UsdPreviewSurface fallback materials; MDL shading is not reproduced."
    )
    if source["mdl"]["externalImports"]:
        report["approximations"].append(
            "MDL files import NVIDIA core libraries outside the package; "
            "the MDL render path is not supported."
        )
    derivation = {
        "recipe": RECIPE,
        "toolVersions": _tool_versions(),
        "selectedVariant": report["variant"],
        "upstreamValidation": source["upstreamValidation"],
        "approximations": report["approximations"],
        "gaps": report["gaps"],
        "textures": sorted(texture_names),
        "skippedGeometry": skipped,
    }
    if report.get("sourceNotice") is not None:
        derivation["sourceNotice"] = report["sourceNotice"]
    _write_provenance(output_dir, candidate, source, derivation)
    from packages.library.metadata import asset_metadata

    asset_metadata(output_dir)
    report["output"]["bytes"] = sum(path.stat().st_size for path in output_dir.rglob("*") if path.is_file())
    report["output"]["files"] = len(_package_files(output_dir))
    report["status"] = "pass" if collision and mass is not None and not gaps else "gap"
    return report


def package_tar(package_dir):
    import io
    import tarfile

    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in sorted(Path(package_dir).rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(package_dir).as_posix()
            info = tarfile.TarInfo(relative)
            info.size = path.stat().st_size
            with path.open("rb") as source:
                archive.addfile(info, source)
    return stream.getvalue()


def import_package(package_dir, state_root, library_root, *, structural_only=False):
    import io

    from packages.library.archive import MAX_UPLOAD_BYTES
    from packages.library.assets import AssetLibrary
    from packages.library.common import LibraryError
    from packages.library.scenes import SceneLibrary
    from packages.library.validation import StructuralValidator

    raw_bytes = sum(path.stat().st_size for path in Path(package_dir).rglob("*") if path.is_file())
    if raw_bytes > MAX_UPLOAD_BYTES:
        return {
            "status": "gap",
            "reason": (
                f"Adapted package is {raw_bytes} bytes before tar headers; "
                f"the existing upload limit is {MAX_UPLOAD_BYTES} bytes"
            ),
        }
    data = package_tar(package_dir)
    if len(data) > MAX_UPLOAD_BYTES:
        return {
            "status": "gap",
            "reason": (
                f"Adapted tar is {len(data)} bytes; "
                f"the existing upload limit is {MAX_UPLOAD_BYTES} bytes"
            ),
        }
    library = AssetLibrary(state_root, user_root=library_root)

    def importer():
        if not structural_only:
            return library.import_tar(io.BytesIO(data))
        from packages.library.imports import LibraryImports

        scenes = SceneLibrary(
            builtin_root=library.roots["builtin"],
            user_root=library.roots["user"],
            state_root=library.jobs_root.parent,
        )

        class StructureOnly:
            def __init__(self, validator):
                self.validator = validator

            def run(self, *values, **options):
                return self.validator.run_structure(*values, **options)

        validator = StructureOnly(StructuralValidator(library.jobs_root.parent))
        return LibraryImports(library, scenes, validator).import_tar(io.BytesIO(data))

    try:
        job = importer()
    except LibraryError as exc:
        return {"status": "fail", "reason": exc.detail.get("message", str(exc)), "detail": exc.detail}
    checks = job["validation"].get("checks", [])
    physics_status = (job["validation"].get("physics") or {}).get("status")
    metadata = yaml.safe_load((Path(package_dir) / "asset.yaml").read_text(encoding="utf-8"))
    collision = (metadata.get("physics") or {}).get("collision") == "authored"
    structural_failures = [
        check for check in checks if check["id"].startswith("S") and check["status"] == "fail"
    ]
    validation_failures = [check for check in checks if check["status"] == "fail"]
    structural = {check["id"]: check["status"] for check in checks if check["id"].startswith("S")}
    physics = {check["id"]: check["status"] for check in checks if check["id"].startswith("P")}
    usable = all(structural.get(f"S{index}") == "pass" for index in USABLE_STRUCTURE)
    static = (
        usable
        and collision
        and all(physics.get(identifier) == "pass" for identifier in STATIC_PHYSICS)
        if physics_status == "completed"
        else "unknown"
    )
    return {
        "status": "pass" if not structural_failures else "fail",
        "reason": "; ".join(check["message"] for check in structural_failures),
        "assetVersion": job.get("assetVersion"),
        "contentHash": job.get("contentHash"),
        "deduplicated": job.get("deduplicated"),
        "uploadBytes": len(data),
        "checks": checks,
        "structural": structural,
        "physics": physics,
        "physicsStatus": physics_status,
        "collisionAuthored": collision,
        "validationFailures": [
            {"id": check["id"], "message": check["message"]} for check in validation_failures
        ],
        "dressingUsable": usable,
        "staticCollisionUsable": static,
    }
