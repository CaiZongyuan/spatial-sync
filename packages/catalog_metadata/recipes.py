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


def simready_object_preview_path(package, path):
    """Native package-model thumbnails exclude texture/source-asset thumbnails."""
    return (
        isinstance(path, str)
        and path.startswith(package + "/simready_usd/.thumbs/")
        and preview_choice([{"path": path, "size": 0}]) is not None
    )


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
    evidence = [value["id"] for value in fields.get("evidence", [])]
    provenance = {
        field: {"kind": "publisher" if evidence else "extracted", "evidence": evidence}
        for field in ("name", "aliases", "summary", "category", "tags", "domains", "uses", "facts", "preview")
        if field in fields
    }
    if (
        fields.get("name", {}).get("en") == _name(upstream_id)
        or fields.get("name", {}).get("en") == upstream_id
    ):
        provenance["name"] = {"kind": "extracted", "method": "upstream-identifier", "evidence": evidence}
    provenance.update(fields.pop("fieldProvenance", {}))
    return normalized_entry(
        profile["id"],
        revision,
        {
            "upstreamId": upstream_id,
            "fieldProvenance": provenance,
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


def _simready_fields(package):
    return {
        "name": {"en": _name(package["path"]), "zh": ""},
        "contentDigest": package.get("oid"),
        "facts": {
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
        },
        "evidence": [],
        "preview": {"state": "not_collected", "role": None, "path": None, "sha256": None},
    }


def _simready(hub, profile, revision):
    root = list(hub.tree(profile, revision))
    packages = [value for value in root if value["type"] == "directory" and not value["path"].startswith(".")]
    if not packages:
        raise MetadataError("inventory_incomplete", "SimReady identity enumeration was empty", 409)
    manifest = {
        "collectionId": profile["id"],
        "revision": revision,
        "complete": True,
        "evidence": [],
        "recipeVersion": RECIPE_VERSION,
        "counts": {
            "entries": len(packages),
            "expectedEntries": len(packages),
            "packages": len(packages),
            "extracted": 0,
            "extractionFailures": 0,
            "files": None,
        },
        "entries": [
            _entry(hub, profile, revision, package["path"], **_simready_fields(package))
            for package in packages
        ],
    }
    hub.notify({"inventory": manifest})
    entries, errors, file_total = [], [], 0
    for package in packages:
        identity = package["path"]
        fields = _simready_fields(package)
        facts = fields["facts"]
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
                if isinstance(manifest.get("license"), str):
                    facts["license"] = {
                        "name": manifest["license"],
                        "scope": "publisher package manifest",
                        "evidencePath": manifest_path,
                    }
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
            object_files = [value for value in files if simready_object_preview_path(identity, value["path"])]
            fields["preview"] = _preview(hub, profile, revision, preview_choice(object_files))
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
            if exc.code in ("budget_exhausted", "maintenance_cancelled", "checkpoint_failed"):
                raise
            errors.append({"upstreamId": identity, "code": exc.code, "message": exc.message})
            fields["extractionError"] = {"code": exc.code, "message": exc.message}
            fields["preview"]["state"] = "failed"
        entry = _entry(hub, profile, revision, identity, **fields)
        entry["extractionStatus"] = "failed" if "extractionError" in entry else "extracted"
        # extractionStatus is part of the source input, so bind it before any
        # task or checkpoint receives the normalized entry.
        entry.pop("inputDigest")
        entry = normalized_entry(profile["id"], revision, entry)
        entries.append(entry)
        hub.notify({"entry": entry})
    return (
        entries,
        {
            "entries": len(entries),
            "expectedEntries": len(packages),
            "packages": len(packages),
            "files": file_total,
            "extractionFailures": len(errors),
            "extracted": len(entries) - len(errors),
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
        return rows, 0
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise MetadataError(
            "table_decoder_unconfigured",
            "Scenario Parquet needs an explicitly configured pyarrow environment",
            409,
        ) from exc
    try:
        file = parquet.ParquetFile(io.BytesIO(data))
        compressed_preview_bytes = 0
        for row_index in range(file.metadata.num_row_groups):
            group = file.metadata.row_group(row_index)
            for column_index in range(group.num_columns):
                column = group.column(column_index)
                if column.path_in_schema == "preview" or column.path_in_schema.startswith("preview."):
                    compressed_preview_bytes += column.total_compressed_size
        table = file.read()
        if table.num_rows > 100000:
            raise MetadataError("source_inventory_limit", "Metadata table exceeds 100000 rows")
        return table.to_pylist(), compressed_preview_bytes
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
        before_network = hub.usage["networkBytes"]
        data = hub.read_file(
            profile, revision, file["path"], content_digest=file.get("lfs", {}).get("oid") or file.get("oid")
        )
        proof = _evidence(hub, profile, revision, file["path"], data)
        evidence.append(proof)
        decoded_rows, compressed_preview_bytes = _decode_rows(data, file["path"])
        if hub.usage["networkBytes"] > before_network:
            hub.usage["embeddedPreviewBytes"] += compressed_preview_bytes
            hub.usage["previewBytes"] += compressed_preview_bytes
            hub.notify()
        for row in decoded_rows:
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
                key = hub.cache(embedded_bytes, kind="preview")
                hub.usage["embeddedPreviewDecodedBytes"] += len(embedded_bytes)
                preview.update(state="available", cachedSha256=key, embedded=True)
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
            "outputFiles": len(seen_outputs),
            "variants": output_count,
            "shards": len(files),
        },
        evidence,
        [],
    )


def _chemlab_indexes(hub, profile, revision):
    evidence = []
    try:
        catalog, proof = _read_json(hub, profile, revision, "catalog/catalog.json")
        rows = catalog.get("assets") if isinstance(catalog, dict) else None
        if not isinstance(rows, list):
            raise MetadataError("invalid_source_table", "ChemLab instrument catalog must enumerate assets")
    except MetadataError as exc:
        if exc.code != "source_missing":
            raise
        data = hub.read_file(profile, revision, "catalog/catalog.csv")
        proof = _evidence(hub, profile, revision, "catalog/catalog.csv", data)
        try:
            rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))
        except (UnicodeError, csv.Error) as exc:
            raise MetadataError("invalid_source_table", "ChemLab catalog CSV is invalid") from exc
    evidence.append(proof)
    indexed = {row.get("id"): row for row in rows}
    if len(indexed) != len(rows) or not all(isinstance(key, str) for key in indexed):
        raise MetadataError("duplicate_identity", "ChemLab instrument catalog repeats or omits identity")
    labware_count = 0
    try:
        index, proof = _read_json(hub, profile, revision, "assets/labware/asset_index.json")
    except MetadataError as exc:
        if exc.code != "source_missing":
            raise
        return indexed, evidence, len(rows), labware_count
    if not isinstance(index, dict) or not isinstance(index.get("assets"), list):
        raise MetadataError("invalid_source_table", "ChemLab utensil/furniture index must enumerate assets")
    evidence.append(proof)
    try:
        rich, rich_proof = _read_json(hub, profile, revision, "assets/labware/catalog.json")
        if not isinstance(rich, dict):
            raise MetadataError("invalid_source_table", "ChemLab labware catalog must be structured")
        evidence.append(rich_proof)
    except MetadataError as exc:
        if exc.code != "source_missing":
            raise
        rich = {}
    extra = {**rich.get("types", {}), **rich.get("furniture", {})}
    for asset in index["assets"]:
        identity = asset.get("asset_type_id")
        if not isinstance(identity, str) or identity in indexed:
            raise MetadataError("duplicate_identity", "ChemLab union repeats or omits identity")
        details = extra.get(identity, {})
        contract = asset.get("contract_path")
        path = details.get("dir") or (
            "assets/labware/" + str(PurePosixPath(contract).parent) if contract else None
        )
        indexed[identity] = {
            "id": identity,
            "name": details.get("name_en"),
            "name_zh": asset.get("name_zh"),
            "path": path,
            "size_m": details.get("size_m"),
            "files": {kind: details.get("model_" + kind) for kind in ("glb", "urdf", "blend")},
            "maker": details.get("maker"),
            "model": details.get("model"),
            "n_components": asset.get("component_count"),
            "n_joints": len(details["joints"]) if isinstance(details.get("joints"), dict) else None,
            "sourceCategory": asset.get("category"),
            "sourceLicense": details.get("licence"),
            "glb_up": "z" if rich.get("conventions", {}).get("up_axis") == "+Z" else None,
            "sourceIndex": asset,
            "sourceCatalog": details,
        }
        labware_count += 1
    if index.get("asset_count", labware_count) != labware_count:
        raise MetadataError(
            "inventory_incomplete", "ChemLab utensil/furniture index count is inconsistent", 409
        )
    return indexed, evidence, len(rows), labware_count


def _chemlab(hub, profile, revision):
    manifest, proof = _read_json(hub, profile, revision, "release_manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), dict):
        raise MetadataError(
            "invalid_source_manifest", "ChemLab release manifest must enumerate its asset identity map"
        )
    raw_assets = manifest["assets"]
    hub.notify(
        {
            "inventory": {
                "collectionId": profile["id"],
                "revision": revision,
                "recipeVersion": RECIPE_VERSION,
                "complete": True,
                "evidence": [proof],
                "counts": {"entries": len(raw_assets), "expectedEntries": len(raw_assets), "extracted": 0},
                "entries": [
                    _entry(
                        hub,
                        profile,
                        revision,
                        identity,
                        evidence=[proof],
                        sourceMetadata={"release": declaration},
                    )
                    for identity, declaration in sorted(raw_assets.items())
                ],
            }
        }
    )
    indexed, catalog_evidence, catalog_rows, labware_rows = _chemlab_indexes(hub, profile, revision)
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
    if any(key not in raw_assets for key in indexed):
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
        raw_size = row.get("size_m")
        dimensions = (
            [_finite_number(value, positive=True) for value in raw_size]
            if isinstance(raw_size, list) and len(raw_size) == 3
            else [_finite_number(row.get("size_" + axis + "_m"), positive=True) for axis in "xyz"]
        )
        if any(value is None for value in dimensions):
            dimensions = None
        source_files = row.get("files", row)
        formats = [name for name in ("glb", "urdf", "blend") if source_files.get(name)]
        variants = [{"id": name, "format": name, "path": source_files[name]} for name in formats]
        category = {"instrument": "instrument", "furniture": "furniture", "labware": "labware"}.get(
            raw_category
        )
        preview_files = files + [
            value for value in tree if value["path"].startswith("previews/" + suffix + "/")
        ]
        explicit_preview = row.get("thumb") or row.get("hero")
        if explicit_preview:
            safe_path(explicit_preview)
            matching = next((value for value in tree if value["path"] == explicit_preview), None)
            preview_files.append({**(matching or {}), "path": explicit_preview, "role": "object"})
        choice = preview_choice(preview_files, explicit=explicit_preview)
        entries.append(
            _entry(
                hub,
                profile,
                revision,
                identity,
                name={"en": row.get("name") or _name(suffix or identity), "zh": row.get("name_zh") or ""},
                aliases={
                    "en": [v for v in (row.get("chem_id"), row.get("maker"), row.get("model")) if v],
                    "zh": [row["domain_zh"]] if row.get("domain_zh") else [],
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
                evidence=([proof, *catalog_evidence] if row else [proof])
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
    for entry in entries:
        hub.notify({"entry": entry})
    return (
        entries,
        {
            "entries": len(entries),
            "expectedEntries": len(raw_assets),
            "catalogRows": catalog_rows,
            "labwareRows": labware_rows,
            "metadataRecords": len(indexed),
            "metadataMissing": len(raw_assets) - len(indexed),
            "subsets": dict(categories),
            "files": actual_files,
        },
        [proof, *catalog_evidence] + ([license_proof] if license_proof else []),
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
