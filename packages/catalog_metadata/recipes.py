"""Three concrete dataset structures, sharing one pinned Hub transport.

Recipes import declared fields. They do not use a language model or turn a
publisher's dimensions, license or validation claim into local admission.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import Counter
from pathlib import PurePosixPath
from urllib.parse import quote

from .hub import IMAGE_SUFFIXES, safe_path
from .models import RECIPE_VERSION, MetadataError, digest, normalized_entry

TEXTURE = re.compile(
    r"(?:^|[/_. -])(normal|normals|nrm|roughness|metallic|metalness|albedo|diffuse|"
    r"basecolor|base_color|opacity|occlusion|displacement|height|texture|textures|uv)(?:[/_. -]|$)",
    re.I,
)
OBJECT_IMAGE = re.compile(r"thumb|preview|render|portrait|screenshot|\.thumbs", re.I)


def preview_choice(files, *, explicit=None):
    candidates = []
    for value in files:
        path = value.get("path")
        if not isinstance(path, str) or PurePosixPath(path).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if TEXTURE.search(path):
            continue
        role = value.get("role")
        if role in ("texture", "normal", "material", "input", "source"):
            continue
        if role != "object" and path != explicit and not OBJECT_IMAGE.search(path):
            continue
        size = value.get("size")
        candidates.append((size if type(size) is int and size >= 0 else float("inf"), path, value))
    return min(candidates, default=(None, None, None), key=lambda value: (value[0], value[1]))[2]


def _file_url(hub, profile, revision, path):
    return f"{hub.origin}/datasets/{profile['dataset']}/resolve/{revision}/{quote(path, safe='/')}"


def _evidence(hub, profile, revision, path, data, *, fields=None):
    key = hub.cache(data)
    return {
        "id": key,
        "kind": "publisher",
        "origin": profile["dataset"],
        "dataset": profile["dataset"],
        "revision": revision,
        "path": path,
        "sha256": key,
        "bytes": len(data),
        "url": _file_url(hub, profile, revision, path),
        "fields": fields or [],
    }


def _read_json(hub, profile, revision, path, *, content_digest=None):
    data = hub.read_file(profile, revision, path, content_digest=content_digest)
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise MetadataError("invalid_source_json", f"Invalid metadata JSON: {path}") from exc
    return value, _evidence(hub, profile, revision, path, data)


def _preview(hub, profile, revision, choice):
    if choice is None:
        return {"state": "missing", "role": None, "path": None, "sha256": None}
    path = safe_path(choice["path"])
    sha = choice.get("sha256")
    if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha):
        sha = "sha256:" + sha
    return {
        "state": "not_collected",
        "role": "object",
        "path": path,
        "sha256": sha,
        "dataset": profile["dataset"],
        "revision": revision,
        "url": _file_url(hub, profile, revision, path),
        "sourceBytes": choice.get("size"),
    }


def _entry(hub, profile, revision, upstream_id, **fields):
    return normalized_entry(
        profile["id"],
        revision,
        {
            "upstreamId": upstream_id,
            "source": {
                "provider": "huggingface",
                "dataset": profile["dataset"],
                "url": f"{hub.origin}/datasets/{profile['dataset']}/tree/{revision}",
                "recipe": profile["recipe"],
                "recipeVersion": RECIPE_VERSION,
            },
            **fields,
        },
    )


def _name(identifier):
    return re.sub(r"\s+", " ", identifier.replace("_", " ").replace("-", " ")).strip()


def _finite_number(value, *, positive=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        return None
    return number


def _integer(value):
    number = _finite_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _simready(hub, profile, revision):
    root = list(hub.tree(profile, revision))
    packages = [value for value in root if value["type"] == "directory" and not value["path"].startswith(".")]
    entries, errors, file_total = [], [], 0
    for package in packages:
        identity = package["path"]
        facts = {
            "formats": ["usd"],
            "sourceBytes": None,
            "fileCount": None,
            "dimensionsM": None,
            "unit": None,
            "coordinateSystem": None,
            "parts": None,
            "joints": None,
            "license": None,
            "variants": [],
        }
        fields = {
            "name": {"en": _name(identity), "zh": ""},
            "contentDigest": package.get("oid"),
            "facts": facts,
            "evidence": [],
            "preview": {"state": "not_collected", "role": None, "path": None, "sha256": None},
        }
        try:
            locator = {
                "kind": "package-tree",
                "dataset": profile["dataset"],
                "path": identity,
                "treeOid": package.get("oid"),
            }
            cached_tree = hub._cached(locator) if package.get("oid") else None
            if cached_tree is not None:
                files = json.loads(cached_tree[0])
            else:
                files = [
                    value
                    for value in hub.tree(profile, revision, identity, recursive=True)
                    if value["type"] == "file"
                ]
                if package.get("oid"):
                    hub.cache(json.dumps(files, sort_keys=True).encode(), locator=locator)
            file_total += len(files)
            facts["fileCount"] = len(files)
            if all(type(value.get("size")) is int and value["size"] >= 0 for value in files):
                facts["sourceBytes"] = sum(value["size"] for value in files)
            manifest_path = identity + "/com.nvidia.simready.packaging.json"
            paths = {value["path"] for value in files}
            if manifest_path in paths:
                manifest_file = next(value for value in files if value["path"] == manifest_path)
                manifest, evidence = _read_json(
                    hub, profile, revision, manifest_path, content_digest=manifest_file.get("oid")
                )
                fields["evidence"].append(evidence)
                if not isinstance(manifest, dict):
                    raise MetadataError(
                        "invalid_source_manifest", "SimReady package manifest must be an object"
                    )
                fields["sourceMetadata"] = manifest
                label = manifest.get("name") or manifest.get("displayName")
                if isinstance(label, str) and label.strip():
                    fields["name"]["en"] = label.strip()
                description = manifest.get("description")
                if isinstance(description, str):
                    fields["summary"] = {"en": description[:8192], "zh": ""}
                tags = manifest.get("tags")
                if isinstance(tags, list):
                    fields["tags"] = [
                        value for value in tags[:64] if isinstance(value, str) and value.strip()
                    ]
            fields["preview"] = _preview(hub, profile, revision, preview_choice(files))
            facts["variants"] = [
                {
                    "id": value["path"],
                    "format": PurePosixPath(value["path"]).suffix.lstrip("."),
                    "path": value["path"],
                    "sourceBytes": value.get("size"),
                    "contentDigest": value.get("lfs", {}).get("oid") or value.get("oid"),
                }
                for value in files
                if re.search(r"/simready_usd/[^/]+\.usd[ac]?$", value["path"])
            ]
        except MetadataError as exc:
            if exc.code in ("budget_exhausted", "maintenance_cancelled", "source_access_restricted"):
                raise
            errors.append({"upstreamId": identity, "code": exc.code, "message": exc.message})
            fields["extractionError"] = {"code": exc.code, "message": exc.message}
            fields["preview"]["state"] = "failed"
        entries.append(_entry(hub, profile, revision, identity, **fields))
    return (
        entries,
        {
            "entries": len(entries),
            "expectedEntries": len(packages),
            "packages": len(packages),
            "files": file_total,
            "extractionFailures": len(errors),
        },
        [],
        errors,
    )


def _decode_rows(data, path):
    if path.endswith(".json"):
        value = json.loads(data)
        rows = value.get("rows") if isinstance(value, dict) else value
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise MetadataError("invalid_source_table", "A metadata table must contain rows")
        return rows
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise MetadataError(
            "table_decoder_unconfigured",
            "Scenario Parquet needs an explicitly configured pyarrow environment",
            409,
        ) from exc
    try:
        table = parquet.read_table(io.BytesIO(data))
        if table.num_rows > 100000:
            raise MetadataError("source_inventory_limit", "Metadata table exceeds 100000 rows")
        return table.to_pylist()
    except MetadataError:
        raise
    except Exception as exc:
        raise MetadataError("invalid_source_table", "Unable to decode the pinned Parquet metadata") from exc


def _scenario(hub, profile, revision):
    files = [
        value
        for value in hub.tree(profile, revision, "data", recursive=True)
        if value["type"] == "file" and value["path"].endswith((".parquet", ".json"))
    ]
    if not files:
        raise MetadataError("inventory_incomplete", "No complete Scenario metadata shards were found", 409)
    entries, evidence, output_count, seen_outputs = [], [], 0, set()
    try:
        license_data = hub.read_file(profile, revision, "README.md")
        license_proof = _evidence(hub, profile, revision, "README.md", license_data, fields=["facts.license"])
        evidence.append(license_proof)
        declared_license = "CC-BY-4.0" if b"license: cc-by-4.0" in license_data.lower() else None
    except MetadataError as exc:
        if exc.code != "source_missing":
            raise
        declared_license, license_proof = None, None
    for file in sorted(files, key=lambda value: value["path"]):
        data = hub.read_file(profile, revision, file["path"])
        proof = _evidence(hub, profile, revision, file["path"], data)
        evidence.append(proof)
        for row in _decode_rows(data, file["path"]):
            identity = row.get("id") or row.get("example_id") or row.get("uid")
            if identity is None:
                raise MetadataError(
                    "missing_source_identity", "Scenario row lacks its stable example identity"
                )
            identity = str(identity)
            outputs = row.get("outputs") or []
            if not isinstance(outputs, list):
                raise MetadataError("invalid_source_table", "Scenario outputs must be a list")
            variants = []
            for output in outputs:
                if not isinstance(output, dict):
                    raise MetadataError("invalid_source_table", "Scenario output must be structured")
                path = output.get("path") or output.get("relative_path") or output.get("file")
                if path:
                    safe_path(path)
                    if path in seen_outputs:
                        raise MetadataError("duplicate_output", "Scenario output file is repeated")
                    seen_outputs.add(path)
                variants.append(
                    {
                        "id": str(output.get("id") or path or len(variants)),
                        "path": path,
                        "format": output.get("format")
                        or (PurePosixPath(path).suffix.lstrip(".") if path else None),
                        "role": output.get("role"),
                        "sha256": output.get("sha256"),
                        "sourceBytes": output.get("size_bytes") or output.get("bytes"),
                    }
                )
            output_count += len(variants)
            prompt = row.get("prompt") or row.get("inputs", {}).get("prompt") or ""
            prompt = prompt if isinstance(prompt, str) else ""
            preview_file = row.get("preview_file") or {}
            if isinstance(preview_file, str):
                preview_file = {"path": preview_file}
            embedded = row.get("preview") or {}
            embedded_bytes = embedded.get("bytes") if isinstance(embedded, dict) else None
            choice = {**preview_file, "role": "object"} if preview_file.get("path") else None
            preview = _preview(hub, profile, revision, choice)
            if isinstance(embedded_bytes, bytes):
                key = hub.cache(embedded_bytes)
                hub.usage["embeddedPreviewBytes"] += len(embedded_bytes)
                hub.usage["previewBytes"] += len(embedded_bytes)
                preview.update(state="available", sha256=key, embedded=True)
                if preview.get("path") is None:
                    preview.update(path=embedded.get("path"), role="object")
            safe_row = {key: value for key, value in row.items() if key != "preview"}
            entries.append(
                _entry(
                    hub,
                    profile,
                    revision,
                    identity,
                    name={"en": row.get("name") or _name(identity), "zh": row.get("name_zh") or ""},
                    summary={"en": prompt[:8192], "zh": ""},
                    contentDigest=digest({"outputs": outputs, "inputs": row.get("inputs"), "prompt": prompt}),
                    sourceMetadata=safe_row,
                    evidence=[proof] + ([license_proof] if license_proof else []),
                    preview=preview,
                    facts={
                        "formats": sorted({value["format"] for value in variants if value["format"]}),
                        "fileCount": len(variants),
                        "variants": variants,
                        "sourceBytes": sum(v["sourceBytes"] for v in variants)
                        if all(type(v["sourceBytes"]) is int for v in variants)
                        else None,
                        "dimensionsM": None,
                        "unit": None,
                        "coordinateSystem": None,
                        "license": {
                            "name": declared_license,
                            "attribution": "Scenario",
                            "scope": "dataset declaration",
                        }
                        if declared_license
                        else None,
                    },
                )
            )
    return (
        entries,
        {
            "entries": len(entries),
            "expectedEntries": len(entries),
            "tableRows": len(entries),
            "outputFiles": output_count,
            "variants": output_count,
            "shards": len(files),
        },
        evidence,
        [],
    )


def _chemlab(hub, profile, revision):
    manifest, proof = _read_json(hub, profile, revision, "release_manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), dict):
        raise MetadataError(
            "invalid_source_manifest", "ChemLab release manifest must enumerate its asset identity map"
        )
    raw_assets = manifest["assets"]
    data = hub.read_file(profile, revision, "catalog/catalog.csv")
    catalog_proof = _evidence(hub, profile, revision, "catalog/catalog.csv", data)
    try:
        license_data = hub.read_file(profile, revision, "LICENSE-ASSETS.md")
        license_proof = _evidence(
            hub, profile, revision, "LICENSE-ASSETS.md", license_data, fields=["facts.license"]
        )
        declared_license = "CC-BY-NC-4.0" if b"CC BY-NC 4.0" in license_data else None
    except MetadataError as exc:
        if exc.code != "source_missing":
            raise
        declared_license, license_proof = None, None
    try:
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    except (UnicodeError, csv.Error) as exc:
        raise MetadataError("invalid_source_table", "ChemLab catalog CSV is invalid") from exc
    indexed = {value.get("id"): value for value in rows}
    if len(indexed) != len(rows) or any(not key or key not in raw_assets for key in indexed):
        raise MetadataError(
            "inventory_incomplete", "ChemLab table identities do not reconcile with its release union", 409
        )
    # The release union is authoritative. The 203-row instrument catalog cannot
    # silently hide utensils or furniture that lack a table row.
    tree = [value for value in hub.tree(profile, revision, recursive=True) if value["type"] == "file"]
    entries, categories, actual_files = [], Counter(), 0
    for identity, declaration in sorted(raw_assets.items()):
        row = indexed.get(identity, {})
        raw_category, _, suffix = identity.partition(".")
        categories[raw_category] += 1
        prefix = row.get("path")
        if not prefix:
            matches = sorted(
                {
                    str(PurePosixPath(value["path"]).parent)
                    for value in tree
                    if PurePosixPath(value["path"]).parent.name == suffix
                }
            )
            prefix = matches[0] if len(matches) == 1 else None
        files = [value for value in tree if prefix and value["path"].startswith(prefix.rstrip("/") + "/")]
        actual_files += len(files)
        dimensions = [_finite_number(row.get("size_" + axis + "_m"), positive=True) for axis in "xyz"]
        if any(value is None for value in dimensions):
            dimensions = None
        formats = [name for name in ("glb", "urdf") if row.get(name)]
        variants = [{"id": name, "format": name, "path": row[name]} for name in formats]
        category = {"instrument": "instrument", "furniture": "furniture", "labware": "labware"}.get(
            raw_category
        )
        preview_files = files + [
            value for value in tree if value["path"].startswith("previews/" + suffix + "/")
        ]
        choice = preview_choice(preview_files)
        entries.append(
            _entry(
                hub,
                profile,
                revision,
                identity,
                name={"en": row.get("name") or _name(suffix or identity), "zh": row.get("name_zh") or ""},
                aliases={
                    "en": [v for v in (row.get("chem_id"), row.get("maker"), row.get("model")) if v],
                    "zh": [],
                },
                category=category,
                domains=[row["domain_name"]] if row.get("domain_name") else [],
                tags=["laboratory", raw_category],
                sourceMetadata={"catalog": row, "release": declaration},
                contentDigest=digest(
                    {
                        "catalog": row,
                        "release": declaration,
                        "files": [(v["path"], v.get("oid")) for v in files],
                    }
                ),
                evidence=([proof, catalog_proof] if row else [proof])
                + ([license_proof] if license_proof else []),
                preview=_preview(hub, profile, revision, choice),
                facts={
                    "formats": formats,
                    "variants": variants,
                    "dimensionsM": dimensions,
                    "unit": "m" if dimensions else None,
                    "coordinateSystem": {"up": row.get("glb_up")} if row.get("glb_up") else None,
                    "parts": _integer(row.get("n_components")),
                    "joints": _integer(row.get("n_joints")),
                    "fileCount": len(files) if files else None,
                    "declaredShippedFiles": declaration.get("shipped")
                    if isinstance(declaration, dict)
                    else None,
                    "sourceBytes": sum(v["size"] for v in files)
                    if files and all(type(v.get("size")) is int for v in files)
                    else None,
                    "license": {
                        "name": declared_license,
                        "scope": "asset/catalog/preview declaration",
                        "evidencePath": "LICENSE-ASSETS.md",
                        "thirdPartyTerms": "Retained separately in NOTICE.md",
                    }
                    if declared_license
                    else None,
                },
            )
        )
    return (
        entries,
        {
            "entries": len(entries),
            "expectedEntries": len(raw_assets),
            "catalogRows": len(rows),
            "subsets": dict(categories),
            "files": actual_files,
        },
        [proof, catalog_proof] + ([license_proof] if license_proof else []),
        [],
    )


def enumerate_collection(hub, profile, revision):
    recipe = {"simready": _simready, "scenario": _scenario, "chemlab": _chemlab}[profile["recipe"]]
    entries, counts, evidence, errors = recipe(hub, profile, revision)
    if not entries or len({value["upstreamId"] for value in entries}) != len(entries):
        raise MetadataError("inventory_incomplete", "Source inventory is empty or repeats identities", 409)
    return {
        "collectionId": profile["id"],
        "revision": revision,
        "entries": entries,
        "complete": True,
        "counts": counts,
        "evidence": evidence,
        "errors": errors,
        "recipeVersion": RECIPE_VERSION,
    }
