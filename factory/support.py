"""Shape and honesty checks for the restricted Scene Factory support list."""

from __future__ import annotations

import json
from pathlib import Path

from factory.simready import (
    DATASET,
    REVISION,
    SOURCE_PACKAGES,
    STATIC_PHYSICS,
    USABLE_STRUCTURE,
)

REQUIRED_LIMITS = {
    "sourcePackageBytes": 512 * 1024 * 1024,
    "adaptedPackageBytes": 512 * 1024 * 1024,
    "batchBytes": 2 * 1024 * 1024 * 1024,
    "rawCacheBytes": 10 * 1024 * 1024 * 1024,
    "concurrentAcquisitions": 2,
    "freeDiskMinBytes": 10 * 1024 * 1024 * 1024,
    "existingUploadBytes": 128 * 1024 * 1024,
    "existingUnpackBytes": 256 * 1024 * 1024,
}
BLENDER_VERSION = "4.5.9"
MCP_VERSION = "2.2.2"
MCP_COMMIT = "5a3618fccb2d982d0dd61e7a1879912c3100073b"
BLENDER_DOWNLOAD = (
    "https://download.blender.org/release/Blender4.5/blender-4.5.9-windows-x64.zip"
)
ADDON_VERSION = "1.8"
BLENDER_LICENSES = {
    "blender": "GPL-3.0-or-later",
    "mcpForBlender": "MIT",
    "addon": "MIT",
}


def empty(checked_at):
    return {
        "spec": "LAB_SCENE_FACTORY_SPEC_V1",
        "checkedAt": checked_at,
        "source": {"dataset": DATASET, "revision": REVISION},
        "limits": dict(REQUIRED_LIMITS),
        "blender": {
            "version": BLENDER_VERSION,
            "mcpForBlender": MCP_VERSION,
            "mcpCommit": MCP_COMMIT,
            "blenderDownload": BLENDER_DOWNLOAD,
            "addon": {"name": "MCP for Blender", "version": ADDON_VERSION},
            "license": dict(BLENDER_LICENSES),
            "configuration": {
                "safeMode": "BLENDER_MCP_SAFE_MODE=1",
                "telemetry": "DISABLE_TELEMETRY=true",
                "autoStartServer": False,
                "autoStartServerDefault": True,
                "providersEnabled": False,
                "telemetryRuntime": "unavailable (wheel omits blender_mcp.config)",
            },
            "verified": {
                "backgroundScript": False,
                "safeMode": False,
                "telemetryDisabled": False,
                "providersDisabled": False,
            },
            "evidence": None,
        },
        "sourceCandidates": [],
        "limitations": [
            "Only the pinned NVIDIA SimReady package family is prepared; "
            "this is not a general converter.",
            "Only the Newton physics variant and UsdPreviewSurface fallback materials are used; "
            "MDL shading is not reproduced.",
            "MDL files may import NVIDIA core libraries outside the source package; "
            "that render path is reported, not executed.",
            "UDIM/UVTILE texture sets are not supported by the restricted GLB path and are gaps.",
            "Mesh simplification is not performed. An over-budget mesh is a gap, "
            "not a deterministic adaptation.",
            "The existing asset physics loader requires joints to belong to an articulation; "
            "SimReady prop-level fixed joints can fail P1 (recorded for the container).",
            "The existing 128 MiB upload and 256 MiB unpack limits remain; "
            "larger adapted packages need the SF2 internal import path.",
            "Upstream SimReady validation statements are recorded as declarations, "
            "never as platform validation.",
        ],
        "unknowns": [
            "Real Blender acceptance is separate from GPU-free CI.",
            "Scene-level Newton P2/P3 behavior is not established by single-asset adaptation.",
            "The license status of MDL core libraries outside the package "
            "is not established by the package license.",
            "Whether SimReady prop-level joints are intended as articulations "
            "is not established by the source package.",
        ],
        "ci": {
            "command": "uv run --no-sync python factory/verify.py check",
            "lane": "core",
            "expectedAddedSeconds": 10,
            "actualAddedSeconds": None,
        },
    }


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate(data):
    errors = []
    if data.get("spec") != "LAB_SCENE_FACTORY_SPEC_V1":
        errors.append("spec must be LAB_SCENE_FACTORY_SPEC_V1")
    if (data.get("source") or {}).get("dataset") != DATASET:
        errors.append("source dataset differs from the pinned dataset")
    if (data.get("source") or {}).get("revision") != REVISION:
        errors.append("source revision differs from the pinned revision")
    if data.get("limits") != REQUIRED_LIMITS:
        errors.append("budget limits differ from the specification")
    blender = data.get("blender") or {}
    if blender.get("version") != BLENDER_VERSION or blender.get("mcpForBlender") != MCP_VERSION:
        errors.append("Blender/MCP versions are not the pinned support combination")
    if blender.get("mcpCommit") != MCP_COMMIT:
        errors.append("the paired MCP commit is not recorded")
    if (blender.get("addon") or {}).get("version") != ADDON_VERSION:
        errors.append("the paired addon version is not recorded")
    if blender.get("license") != BLENDER_LICENSES:
        errors.append("the separate Blender/MCP/addon licenses are not recorded")
    configuration = blender.get("configuration") or {}
    if (
        configuration.get("autoStartServer") is not False
        or configuration.get("autoStartServerDefault") is not True
    ):
        errors.append("the addon auto-start baseline is not recorded honestly")
    candidates = data.get("sourceCandidates")
    if not isinstance(candidates, list):
        return errors + ["sourceCandidates must be a list"]
    names = [item.get("package") for item in candidates]
    if sorted(names) != sorted(candidate.package for candidate in SOURCE_PACKAGES):
        errors.append("support list must contain exactly the five pinned source packages")
    expected = {candidate.package: candidate for candidate in SOURCE_PACKAGES}
    for item in candidates:
        prefix = str(item.get("package"))
        candidate = expected.get(prefix)
        if item.get("revision") != REVISION:
            errors.append(f"{prefix}: revision is not pinned")
        source = item.get("source") or {}
        if candidate is not None and (
            source.get("expectedFiles") != candidate.expected_files
            or source.get("expectedBytes") != candidate.expected_bytes
            or source.get("files") != candidate.expected_files
            or source.get("bytes") != candidate.expected_bytes
        ):
            errors.append(f"{prefix}: source size does not match the pinned package")
        if not isinstance(source.get("url"), str) or not source["url"].startswith("https://"):
            errors.append(f"{prefix}: source URL is missing")
        if not isinstance(source.get("bomSha256"), str) or len(source["bomSha256"]) != 64:
            errors.append(f"{prefix}: BOM digest is missing")
        adaptation = item.get("adaptation") or {}
        status = adaptation.get("status")
        if status not in ("pass", "gap", "fail"):
            errors.append(f"{prefix}: adaptation status is invalid")
        if status == "pass" and (
            source.get("bomVerified") != source.get("bomItems")
            or source.get("bomMissing")
            or source.get("bomMismatched")
        ):
            errors.append(f"{prefix}: pass has an incomplete BOM closure")
        if status == "pass" and (adaptation.get("variant") or {}).get("selected") != "Newton/Enabled":
            errors.append(f"{prefix}: Newton variant selection is not recorded")
        if status == "pass":
            output = adaptation.get("output") or {}
            visual = adaptation.get("visual") or {}
            if not output.get("bytes") or not visual.get("triangles"):
                errors.append(f"{prefix}: pass has no measured output")
            if adaptation.get("gaps"):
                errors.append(f"{prefix}: pass still lists gaps")
        elif not adaptation.get("gaps"):
            errors.append(f"{prefix}: a non-passing adaptation must name its gap")
        license_info = item.get("license") or {}
        if license_info.get("name") not in ("CC0-1.0", "CC-BY-4.0"):
            errors.append(f"{prefix}: license decision is missing")
        if not isinstance(license_info.get("sha256"), str) or len(license_info["sha256"]) != 64:
            errors.append(f"{prefix}: license evidence hash is missing")
        if not isinstance(license_info.get("inBom"), bool):
            errors.append(f"{prefix}: license BOM membership is not recorded")
        obligations = license_info.get("obligations")
        if not isinstance(obligations, list):
            errors.append(f"{prefix}: license obligations are not recorded")
        elif license_info.get("name") == "CC-BY-4.0" and not {
            "attribution",
            "retain license and notices",
            "mark modifications",
        }.issubset(obligations):
            errors.append(f"{prefix}: CC-BY obligations are incomplete")
        mdl = item.get("mdl") or {}
        if not isinstance(mdl.get("licenseEvidence"), list):
            errors.append(f"{prefix}: MDL attachment license evidence is missing")
        import_info = item.get("import") or {}
        if import_info.get("status") not in ("pass", "gap", "fail"):
            errors.append(f"{prefix}: import status is invalid")
        if import_info.get("physicsStatus") not in ("completed", "skipped", None):
            errors.append(f"{prefix}: physics runtime status is not recorded")
        if status == "pass" and import_info.get("status") == "fail":
            errors.append(f"{prefix}: adapted package failed the existing import")
        structure = import_info.get("structural") or {}
        physics = import_info.get("physics") or {}
        usage = item.get("usage") or {}
        if usage.get("dressing") not in (True, False, "unknown"):
            errors.append(f"{prefix}: dressing usage is not measured or unknown")
        if usage.get("staticCollision") not in (True, False, "unknown"):
            errors.append(f"{prefix}: staticCollision usage is not measured or unknown")
        if usage.get("staticCollision") is True:
            if not all(structure.get(f"S{index}") == "pass" for index in USABLE_STRUCTURE):
                errors.append(f"{prefix}: staticCollision claims S1/S2/S4/S6")
            if not (adaptation.get("physics") or {}).get("collision") == "authored":
                errors.append(f"{prefix}: staticCollision claims an authored collision body")
            if not all(physics.get(identifier) == "pass" for identifier in STATIC_PHYSICS):
                errors.append(f"{prefix}: staticCollision claims Newton P1/P3")
        if usage.get("dressing") is True and not all(
            structure.get(f"S{index}") == "pass" for index in USABLE_STRUCTURE
        ):
            errors.append(f"{prefix}: dressing claims S1/S2/S4/S6")
    verified = blender.get("verified") or {}
    for field in ("backgroundScript", "safeMode", "providersDisabled"):
        if verified.get(field) is not True:
            errors.append(f"blender.{field} must be verified true")
    if verified.get("telemetryDisabled") is not True and "unavailable" not in str(
        configuration.get("telemetryRuntime")
    ):
        errors.append("blender.telemetryDisabled must be verified or explicitly unavailable")
    ci = data.get("ci") or {}
    actual = ci.get("actualAddedSeconds")
    if not isinstance(actual, (int, float)) or actual <= 0:
        errors.append("ci.actualAddedSeconds must record the measured check time")
    if not isinstance(data.get("unknowns"), list) or not data["unknowns"]:
        errors.append("unknowns must be explicitly recorded")
    if not isinstance(data.get("limitations"), list) or not data["limitations"]:
        errors.append("limitations must be explicitly recorded")
    if not data.get("checkedAt"):
        errors.append("checkedAt is missing")
    return errors
