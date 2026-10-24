"""Bounded search candidates, catalog entry documents and read-only estimates."""

from __future__ import annotations

import copy
import hashlib
import json

from packages.catalog.local import local_license, local_unknown, local_usage
from packages.catalog.models import ENTRY_SCHEMA, ESTIMATE_SCHEMA
from packages.library.common import LibraryError


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def serialized_size(value):
    return len(canonical_json(value).encode("utf-8"))


def clip(text, maximum):
    if not isinstance(text, str) or len(text) <= maximum:
        return text
    return text[: maximum - 1] + "…"


def clip_list(values, maximum, length):
    return [clip(value, length) for value in (values or [])[:maximum]]


def installed_document(local_assets, limit=3):
    ordered = sorted(local_assets, key=lambda item: item.version.rev, reverse=True)
    selected = ordered[:limit] if limit is not None else ordered
    versions = []
    for item in selected:
        usage = local_usage(item)
        license_document = local_license(item)
        versions.append({
            "id": item.version.id,
            "contentHash": item.version.content_hash,
            "usage": {
                "dressing": usage["dressing"],
                "staticCollision": usage["staticCollision"],
            },
            "license": {
                "name": license_document["name"],
                "admitted": license_document["admitted"],
                "unknown": license_document["unknown"],
            },
            "unknown": local_unknown(item),
        })
    return {
        "versions": versions,
        "latest": ordered[0].version.id if ordered else None,
        "truncated": len(selected) < len(ordered),
    }


def _local_preview(local_assets):
    for item in sorted(local_assets, key=lambda local: local.version.rev, reverse=True):
        try:
            summary = item.version.summary()
        except (OSError, ValueError, LibraryError):
            continue
        url = summary.get("thumbnailUrl") or summary.get("visualUrl")
        if url:
            return {"kind": "local", "url": url, "reason": "Installed local version preview."}
    return None


def preview_document(local_assets):
    return _local_preview(local_assets) or {
        "kind": "unavailable",
        "url": None,
        "reason": "Source preview is not cached; acquisition resolves it.",
    }


def evidence_document(entry):
    return {
        "source": entry.source.url,
        "license": {
            "file": entry.license.evidence.file,
            "sha256": entry.license.evidence.sha256,
        },
        "licenseAttachments": [item.file for item in entry.license.attachments],
        "usage": {
            name: entry.usage[name].evidence for name in ("dressing", "staticCollision")
        },
        "adaptation": {
            "status": entry.adaptation.status,
            "report": f"reports/{entry.package_id}.json",
        },
        "upstreamValidation": {
            "declaredBy": entry.upstream_validation.declared_by,
            "date": entry.upstream_validation.date,
        },
    }


def estimate_pointer(entry):
    path = (
        f"/api/v1/library/catalog/entries/{entry.provider}/{entry.package_id}/"
        f"{entry.revision}/estimate"
    )
    return {
        "resource": "catalog-estimate",
        "id": entry.id,
        "tool": "catalog_estimate",
        "path": path,
    }


def _base_document(entry, local_assets):
    return {
        "schema": ENTRY_SCHEMA,
        "id": entry.id,
        "ref": entry.ref,
        "provider": entry.provider,
        "packageId": entry.package_id,
        "revision": entry.revision,
        "kind": entry.kind,
        "title": entry.names["en"],
        "names": dict(entry.names),
        "aliases": {language: list(values) for language, values in entry.aliases.items()},
        "descriptions": dict(entry.descriptions),
        "tags": {language: list(values) for language, values in entry.tags.items()},
        "source": entry.source.document(),
        "license": entry.license.document() | {"unknown": False},
        "usage": {name: entry.usage[name].document() for name in ("dressing", "staticCollision")},
        "adaptation": entry.adaptation.document(),
        "upstreamValidation": entry.upstream_validation.document(),
        "acquisition": entry.acquisition.document(),
        "installed": installed_document(local_assets, limit=20),
        "unknown": entry.unknown_attributes(),
        "evidence": evidence_document(entry),
        "preview": preview_document(local_assets),
        "estimate": estimate_pointer(entry),
    }


def document_digest_of(document):
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def _bound(document, maximum):
    """Deterministically shrink preview arrays until the canonical document fits."""
    value = copy.deepcopy(document)
    if serialized_size(value) <= maximum:
        return value
    value["previewTruncated"] = True
    steps = (
        ("license", "attachments"),
        ("source", "bomNotInBom"),
        ("upstreamValidation", "failingFeatures"),
        ("adaptation", "approximations"),
        ("adaptation", "gaps"),
        ("license", "obligations"),
        ("acquisition", "reasons"),
    )
    for parent, key in steps:
        values = value[parent][key]
        while values and serialized_size(value) > maximum:
            values.pop()
    while value["evidence"]["licenseAttachments"] and serialized_size(value) > maximum:
        value["evidence"]["licenseAttachments"].pop()
    for language in ("zh", "en"):
        while value["aliases"][language] and serialized_size(value) > maximum:
            value["aliases"][language].pop()
    for language in ("zh", "en"):
        while value["tags"][language] and serialized_size(value) > maximum:
            value["tags"][language].pop()
    for description_limit in (240, 120, 60):
        if serialized_size(value) > maximum:
            value["descriptions"] = {
                language: clip(text, description_limit)
                for language, text in value["descriptions"].items()
            }
    if serialized_size(value) > maximum:
        for language in ("en", "zh"):
            value["names"][language] = clip(value["names"][language], 160)
        value["title"] = clip(value["title"], 160)
    if serialized_size(value) > maximum:
        for name in ("dressing", "staticCollision"):
            value["usage"][name]["evidence"] = clip(value["usage"][name]["evidence"], 120)
        value["source"]["url"] = clip(value["source"]["url"], 500)
        value["evidence"]["source"] = clip(value["evidence"]["source"], 500)
        value["evidence"]["usage"] = {
            name: clip(text, 120) for name, text in value["evidence"]["usage"].items()
        }
        value["license"]["name"] = clip(value["license"]["name"], 200)
        value["upstreamValidation"]["declaredBy"] = clip(
            value["upstreamValidation"]["declaredBy"], 300
        )
        value["preview"]["reason"] = clip(value["preview"].get("reason"), 240)
    return value


def _summary(base, digest):
    summary = copy.deepcopy(base)
    summary.update(
        view="summary",
        documentDigest=digest,
        previewTruncated=bool(base.get("previewTruncated")),
    )
    summary["details"] = {
        "view": "full",
        "resource": "catalog",
        "id": base["id"],
        "ref": base["ref"],
        "documentDigest": digest,
        "tool": "catalog_show",
    }
    return _bound(summary, 8192)


def entry_document(catalog, entry, local_assets, *, view="summary", document_digest=None):
    # The digest pins the bounded catalog document; the response-envelope fields
    # (view and documentDigest itself) are excluded from the hash.
    base = _bound(_base_document(entry, local_assets), 32768 - 512)
    digest = document_digest_of(base)
    if document_digest is not None and document_digest != digest:
        raise LibraryError(
            "catalog_changed",
            "Fetch a fresh catalog entry; the document changed",
            409,
            documentDigest=digest,
        )
    if view == "full":
        return base | {"view": "full", "documentDigest": digest}
    if view != "summary":
        raise LibraryError("invalid_view", "view must be summary or full", 400)
    return _summary(base, digest)


def estimate_document(catalog, entry, local_assets):
    limits = catalog.snapshot.limits
    source_bytes = entry.source.bytes
    adapted = entry.adaptation.output_bytes
    adapted_upper = adapted if adapted is not None else limits["adaptedPackageBytes"]
    required = source_bytes + adapted_upper + limits["freeDiskMinBytes"]
    within = (
        source_bytes <= limits["sourcePackageBytes"]
        and adapted_upper <= limits["adaptedPackageBytes"]
        and source_bytes <= limits["batchBytes"]
    )
    return {
        "schema": ESTIMATE_SCHEMA,
        "ref": entry.ref,
        "source": {
            "bytes": source_bytes,
            "files": entry.source.files,
            "licenseFiles": [entry.license.evidence.file],
            "bomItems": entry.source.bom_items,
            "bomNotInBom": list(entry.source.bom_not_in_bom),
        },
        "cache": {
            "reusedBytes": None,
            "known": False,
            "reason": "Raw cache is not implemented before SF2.",
        },
        "transfer": {"upperBoundBytes": source_bytes},
        "temporary": {"upperBoundBytes": source_bytes},
        "adaptedPackage": {
            "bytes": adapted,
            "upperBoundBytes": adapted_upper,
            "known": adapted is not None,
        },
        "requiredDiskBytes": required,
        "limits": {key: limits[key] for key in (
            "sourcePackageBytes",
            "adaptedPackageBytes",
            "batchBytes",
            "rawCacheBytes",
            "freeDiskMinBytes",
        )},
        "withinLimits": within,
        "license": {
            "admitted": entry.license.admitted,
            "reasons": list(entry.license.obligations) if entry.license.admitted else [],
        },
        "usage": {name: entry.usage_value(name) for name in ("dressing", "staticCollision")},
        "acquisition": entry.acquisition.document(),
        "unknown": entry.unknown_attributes(),
        "networkRequired": True,
        "downloadsMainFiles": False,
        "estimated": True,
    }


def compact_candidate(candidate):
    value = copy.deepcopy(candidate)
    for key in ("ref", "id"):
        value[key] = clip(value.get(key), 300)
    value["kind"] = clip(value.get("kind"), 50)
    value["title"] = clip(value.get("title"), 200)
    value["names"] = {language: clip(text, 200) for language, text in value["names"].items()}
    value["descriptions"] = {
        language: clip(text, 240) for language, text in value["descriptions"].items()
    }
    source = value["source"]
    source["provider"] = clip(source.get("provider"), 100)
    source["packageId"] = clip(source.get("packageId"), 200)
    source["revision"] = clip(source.get("revision"), 100)
    value["license"]["name"] = clip(value["license"].get("name"), 200)
    value["license"]["obligations"] = clip_list(value["license"]["obligations"], 5, 200)
    value["installed"]["latest"] = clip(value["installed"].get("latest"), 200)
    value["installed"]["versions"] = value["installed"]["versions"][:3]
    for version in value["installed"]["versions"]:
        version["id"] = clip(version.get("id"), 200)
        version["contentHash"] = clip(version.get("contentHash"), 128)
        if isinstance(version.get("license"), dict):
            version["license"]["name"] = clip(version["license"].get("name"), 200)
        version["unknown"] = [clip(item, 80) for item in version.get("unknown", [])[:8]]
    value["match"]["reasons"] = [
        {
            "field": clip(reason["field"], 80),
            "value": clip(reason["value"], 80),
            "query": clip(reason["query"], 80),
        }
        for reason in value["match"]["reasons"][:5]
    ]
    for key in ("satisfied", "unsatisfied", "unknown"):
        value["match"][key] = [clip(item, 80) for item in value["match"][key][:8]]
    value["unknown"] = [clip(item, 80) for item in value["unknown"][:8]]
    value["acquisition"]["reasons"] = clip_list(value["acquisition"]["reasons"], 5, 240)
    value["usage"]["evidence"] = clip(value["usage"].get("evidence"), 240)
    value["detail"]["id"] = clip(value["detail"].get("id"), 300)
    value["preview"]["url"] = clip(value["preview"].get("url"), 500)
    value["preview"]["reason"] = clip(value["preview"].get("reason"), 240)
    local = value.get("local")
    if local is not None:
        local["id"] = clip(local.get("id"), 200)
        local["contentHash"] = clip(local.get("contentHash"), 128)
        local["bytes"] = local.get("bytes")
        asset = local["asset"]
        asset["id"] = clip(asset.get("id"), 200)
        asset["title"] = clip(asset.get("title"), 200)
        asset["kind"] = clip(asset.get("kind"), 50)
        asset["validationStatus"] = clip(asset.get("validationStatus"), 80)
        asset["thumbnailUrl"] = clip(asset.get("thumbnailUrl"), 500)
        asset["visualUrl"] = clip(asset.get("visualUrl"), 500)
        dimensions = asset.get("dimensionsM")
        if isinstance(dimensions, list):
            asset["dimensionsM"] = dimensions[:3]
        asset["implementedTypes"] = clip_list(asset.get("implementedTypes", []), 32, 80)
        asset["gaps"] = clip_list(asset.get("gaps", []), 8, 200)
        completeness = asset.get("completeness")
        if isinstance(completeness, dict):
            completeness["status"] = clip(completeness.get("status"), 80)
            completeness["gaps"] = clip_list(completeness.get("gaps", []), 8, 200)
            items = completeness.get("items", [])[:8]
            for item in items:
                if isinstance(item, dict):
                    item["id"] = clip(item.get("id"), 80)
                    item["status"] = clip(item.get("status"), 80)
                    item["message"] = clip(item.get("message"), 240)
            completeness["items"] = items
    return value
