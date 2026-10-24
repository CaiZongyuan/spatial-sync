"""One entry point for the SF0 support checks.

    uv run --no-sync python factory/verify.py check
    uv run --no-sync python factory/verify.py evidence --cache .scratch/sf0/cache --out .scratch/sf0/evidence
    uv run --no-sync python factory/verify.py blender --blender <path> --mcp <path> --out <evidence.json>

`check` is the GPU-free CI path: a synthetic source, the restricted adapter,
the existing immutable import and the existing structural checks. `evidence`
uses the pinned real sources and is not a CI prerequisite.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import support as support_schema  # noqa: E402
from factory.fixture import build_source  # noqa: E402
from factory.simready import (  # noqa: E402
    REVISION,
    SOURCE_PACKAGE_BY_NAME,
    SOURCE_PACKAGES,
    adapt,
    download_package,
    import_package,
    inspect_source,
)


def _synthetic():
    """Run the synthetic journey and return its errors and wall-clock seconds."""
    started = time.perf_counter()
    errors = []
    with tempfile.TemporaryDirectory(prefix="sf0-check-") as temporary:
        root = Path(temporary)
        candidate, source = build_source(root / "source")
        report = adapt(candidate, source, root / "adapted")
        if report.get("status") != "pass":
            errors.append("synthetic adaptation did not pass: " + "; ".join(report.get("gaps", [])))
        imported = import_package(
            root / "adapted", root / "state", root / "library", structural_only=True
        )
        if imported.get("status") != "pass":
            errors.append("synthetic import did not pass: " + imported.get("reason", ""))
        structural = imported.get("structural") or {}
        for identifier in ("S1", "S2", "S3", "S4", "S5", "S6", "S7"):
            if structural.get(identifier) != "pass":
                errors.append(f"synthetic package did not pass {identifier}")
        if report.get("variant", {}).get("selected") != "Newton/Enabled":
            errors.append("adapter did not select the Newton variant")
    return errors, round(time.perf_counter() - started, 2)


def check():
    """Run the synthetic end-to-end journey and validate the committed support list."""
    synthetic_errors, seconds = _synthetic()
    support_path = REPO_ROOT / "factory" / "support.json"
    support_errors = []
    if not support_path.is_file():
        support_errors.append("factory/support.json is missing")
    else:
        data = support_schema.load(support_path)
        support_errors.extend(support_schema.validate(data))
        candidates = data.get("sourcePackages") or []
        if not any(
            item.get("adaptation", {}).get("status") == "pass"
            and item.get("import", {}).get("status") == "pass"
            for item in candidates
        ):
            support_errors.append(
                "support list has no source package that completed adaptation and import"
            )
    errors = synthetic_errors + support_errors
    if not support_path.is_file():
        support_state = "missing"
    else:
        support_state = "invalid" if support_errors else "checked"
    result = {
        "command": "check",
        "syntheticJourney": "pass" if not synthetic_errors else "fail",
        "syntheticSeconds": seconds,
        "supportList": support_state,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


def _source_entry(candidate, report, imported):
    source = report.get("source") or {}
    bom = source.get("bom") or {}
    adaptation = {
        "status": report.get("status"),
        "gaps": report.get("gaps", []),
        "approximations": report.get("approximations", []),
        "output": report.get("output"),
        "visual": report.get("visual"),
        "dimensionsM": report.get("dimensionsM"),
        "physics": report.get("physics"),
        "sourcePhysics": report.get("sourcePhysics"),
        "variant": report.get("variant"),
    }
    imported = imported or {"status": "gap", "reason": "Adaptation did not produce a package"}
    usage = {
        "dressingUsable": imported.get("dressingUsable", "unknown"),
        "staticCollisionUsable": imported.get("staticCollisionUsable", "unknown"),
        "note": (
            "Static collision follows S1/S2/S4/S6, an authored collision body "
            "and Newton P1/P3; other failing checks stay visible."
        ),
    }
    return {
        "package": candidate.package,
        "plannedLocalAssetId": candidate.asset_id,
        "title": candidate.title,
        "role": candidate.role,
        "revision": REVISION,
        "source": {
            "url": source.get("source"),
            "files": source.get("files"),
            "bytes": source.get("bytes"),
            "expectedFiles": source.get("expectedFiles"),
            "expectedBytes": source.get("expectedBytes"),
            "rootUsd": source.get("rootUsd"),
            "bomItems": bom.get("items"),
            "bomVerified": bom.get("verified"),
            "bomMissing": bom.get("missing"),
            "bomMismatched": bom.get("mismatched"),
            "bomNotInBom": bom.get("notInBom"),
            "bomSha256": bom.get("sha256"),
            "bomBytes": bom.get("bytes"),
        },
        "license": source.get("license"),
        "mdl": {
            "externalImports": (source.get("mdl") or {}).get("externalImports", []),
            "licenseEvidence": (source.get("mdl") or {}).get("licenseEvidence", []),
            "note": "MDL rendering is not supported; UsdPreviewSurface fallback is the supported path.",
        },
        "upstreamValidation": {
            key: (source.get("upstreamValidation") or {}).get(key)
            for key in ("profile", "profileVersion", "date", "failingFeatures", "declaredBy")
        },
        "adaptation": adaptation,
        "import": {
            "status": imported.get("status"),
            "reason": imported.get("reason"),
            "assetVersion": imported.get("assetVersion"),
            "contentHash": imported.get("contentHash"),
            "uploadBytes": imported.get("uploadBytes"),
            "structural": imported.get("structural"),
            "physics": imported.get("physics"),
            "physicsStatus": imported.get("physicsStatus"),
            "validationFailures": imported.get("validationFailures"),
        },
        "usage": usage,
        "evidence": f"reports/{candidate.package}.json",
    }


def _blender_summary(data):
    keys = (
        "version",
        "mcpForBlender",
        "mcpCommit",
        "blenderDownload",
        "addon",
        "license",
        "contentTelemetryConsent",
        "configuration",
        "verified",
        "notes",
    )
    summary = {key: data.get(key) for key in keys if key in data}
    summary["evidence"] = "Blender and MCP probe output is kept in the delivery evidence directory."
    return summary


def evidence(args):
    cache = Path(args.cache)
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    report_root = output / "reports"
    report_root.mkdir(exist_ok=True)
    selected = (
        [SOURCE_PACKAGE_BY_NAME[name] for name in args.package]
        if args.package
        else list(SOURCE_PACKAGES)
    )
    entries = []
    for candidate in selected:
        source = cache / candidate.package
        if not args.skip_download:
            print(f"downloading {candidate.package}", flush=True)
            download_package(candidate.package, cache)
        print(f"inspecting {candidate.package}", flush=True)
        try:
            inspect_source(candidate, source)
        except Exception as exc:
            report = {
                "package": candidate.package,
                "status": "fail",
                "gaps": [f"{type(exc).__name__}: {exc}"],
                "approximations": [],
            }
            (report_root / f"{candidate.package}.json").write_text(
                json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
            )
            entries.append(_source_entry(candidate, report, None))
            continue
        print(f"adapting {candidate.package}", flush=True)
        report = adapt(candidate, source, output / "adapted" / candidate.package)
        (report_root / f"{candidate.package}.json").write_text(
            json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
        )
        imported = None
        if report.get("status") == "pass":
            print(f"importing {candidate.package}", flush=True)
            imported = import_package(
                output / "adapted" / candidate.package, output / "state", output / "library"
            )
            (report_root / f"{candidate.package}.import.json").write_text(
                json.dumps(imported, indent=2, default=str) + "\n", encoding="utf-8"
            )
        entries.append(_source_entry(candidate, report, imported))
    result = support_schema.empty(datetime.now(UTC).isoformat(timespec="seconds"))
    result["sourcePackages"] = sorted(entries, key=lambda item: item["package"])
    synthetic_errors, seconds = _synthetic()
    result["ci"]["actualAddedSeconds"] = seconds
    result["ci"]["measuredScope"] = "synthetic adapter/import journey"
    result["complete"] = len(entries) == len(SOURCE_PACKAGES) and any(
        item["adaptation"]["status"] == "pass" and item["import"]["status"] == "pass"
        for item in entries
    )
    if synthetic_errors:
        result["complete"] = False
    support_errors = support_schema.validate(result)
    if support_errors:
        result["complete"] = False
    blender_evidence = Path(args.blender_evidence) if args.blender_evidence else output / "blender.json"
    if blender_evidence.is_file():
        result["blender"] = _blender_summary(support_schema.load(blender_evidence))
    support_path = output / "support.json"
    support_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if args.support_out:
        Path(args.support_out).write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "support": str(support_path),
                "sourcePackages": len(entries),
                "complete": result["complete"],
                "supportErrors": support_errors,
            },
            indent=2,
        )
    )
    return (
        0
        if result["complete"]
        and not support_errors
        and all(item["adaptation"]["status"] != "fail" for item in entries)
        else 1
    )


def blender(args):
    from factory.blender import verify

    result = verify(Path(args.blender), Path(args.mcp), Path(args.out))
    print(json.dumps(result, indent=2))
    return 0 if all(result["verified"].values()) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="GPU-free synthetic journey and support-list validation")
    evidence_parser = commands.add_parser("evidence", help="Pinned real-source evidence run")
    evidence_parser.add_argument("--cache", default=".scratch/sf0/cache")
    evidence_parser.add_argument("--out", default=".scratch/sf0/evidence")
    evidence_parser.add_argument(
        "--package", action="append", choices=[item.package for item in SOURCE_PACKAGES]
    )
    evidence_parser.add_argument("--skip-download", action="store_true")
    evidence_parser.add_argument("--blender-evidence")
    evidence_parser.add_argument("--support-out")
    blender_parser = commands.add_parser("blender", help="Verify the pinned Blender/MCP combination")
    blender_parser.add_argument("--blender", required=True)
    blender_parser.add_argument("--mcp", required=True)
    blender_parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "check":
        return check()
    if args.command == "evidence":
        return evidence(args)
    return blender(args)


if __name__ == "__main__":
    raise SystemExit(main())
