"""Unified, bounded local/online asset search over one catalog snapshot."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from packages.catalog.local import (
    LocalAsset,
    package_bytes,
)
from packages.catalog.local import (
    local_license as _local_license,
)
from packages.catalog.local import (
    local_source as _local_source,
)
from packages.catalog.local import (
    local_unknown as _local_unknown,
)
from packages.catalog.local import (
    local_usage as _local_usage,
)
from packages.catalog.models import SEARCH_SCHEMA, unavailable_snapshot_document
from packages.catalog.views import (
    canonical_json,
    clip,
    compact_candidate,
    installed_document,
    preview_document,
    serialized_size,
)
from packages.library.common import LibraryError

MAX_BYTES = 32768
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
SCOPES = ("all", "local", "online")
SORTS = ("relevance", "name", "sourceBytes")
UNKNOWN_POLICY = "excluded"

CATALOG_ID = re.compile(
    r"(?P<provider>[a-z0-9][a-z0-9-]*)/(?P<package>[a-z0-9][a-z0-9_.-]*)"
    r"@(?P<revision>[0-9a-f]{40})"
)
LOCAL_ID = re.compile(
    r"(?P<kind>robot|device|labware|environment)/(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"(?:@(?P<revision>[1-9][0-9]*))?"
)
SOFT_TERMS = {
    "small": "en",
    "large": "en",
    "lab": "en",
    "实验室": "zh",
    "小": "zh",
    "大": "zh",
    "轻": "zh",
    "重": "zh",
    "适合实验室": "zh",
}
NUMBER_UNIT = re.compile(
    r"[0-9]+(?:\.[0-9]+)?(?:km|kg|mg|mm|cm|mb|mib|gb|gib|kb|kib|m|g|b)",
    re.IGNORECASE,
)
CJK = re.compile(r"[\u3400-\u9fff]")
WORD = re.compile(r"[a-z0-9]+")


def _contains_keyword(value, keyword):
    """Latin keywords match whole words (with a simple plural); CJK uses substrings."""
    haystack = value.casefold()
    needle = keyword.casefold()
    if CJK.search(needle):
        return needle in haystack
    words = set(WORD.findall(haystack))
    return (
        needle in words
        or needle + "s" in words
        or needle + "es" in words
        or (needle.endswith("s") and needle[:-1] in words)
        or (needle.endswith("es") and needle[:-2] in words)
    )


@dataclass(frozen=True)
class Filters:
    kind: str | None = None
    provider: str | None = None
    usage: str | None = None
    license_admitted: bool | None = None
    installed: bool | None = None
    min_source_bytes: int | None = None
    max_source_bytes: int | None = None
    min_dimension_m: float | None = None
    max_dimension_m: float | None = None

    def applied(self):
        values = (
            ("kind", self.kind),
            ("provider", self.provider),
            ("usage", self.usage),
            ("licenseAdmitted", self.license_admitted),
            ("installed", self.installed),
            ("minSourceBytes", self.min_source_bytes),
            ("maxSourceBytes", self.max_source_bytes),
            ("minDimensionM", self.min_dimension_m),
            ("maxDimensionM", self.max_dimension_m),
        )
        return {name: value for name, value in values if value is not None}

    def names(self):
        return list(self.applied())


@dataclass
class _Match:
    type: str
    entry: object | None
    local: LocalAsset | None
    reasons: list
    score: float
    status: dict
    approximate: bool = False

    def lists(self):
        return (
            [name for name, value in self.status.items() if value is True],
            [name for name, value in self.status.items() if value is False],
            [name for name, value in self.status.items() if value is None],
        )


def _as_utc(value):
    if value is None:
        return datetime.now(UTC)
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _cjk(text):
    return bool(CJK.search(text or ""))


def _number_text(value):
    return f"{value:g}" if isinstance(value, float) else str(value)


def _tokens(text):
    parts = re.split(r"[\s,，;；、\-_]+", text.strip())
    punctuation = "\"'“”‘’()[]{}.,!?？。！"
    return [
        token.strip(punctuation)
        for token in parts
        if token.strip(punctuation)
    ]


def _interpret(text, filters):
    keywords, preferences, notes, numeric = [], [], [], []
    seen = set()
    for token in _tokens(text):
        folded = token.casefold()
        if folded in SOFT_TERMS:
            preferences.append({"term": token, "language": SOFT_TERMS[folded], "applied": False})
            continue
        if NUMBER_UNIT.fullmatch(token):
            numeric.append(token)
            continue
        if folded not in seen:
            seen.add(folded)
            keywords.append(token)
    for preference in preferences:
        term = preference["term"]
        if filters.min_dimension_m is not None:
            notes.append(
                f"Soft preference '{term}' does not relax "
                f"minDimensionM={_number_text(filters.min_dimension_m)}."
            )
        elif filters.max_dimension_m is not None:
            notes.append(
                f"Soft preference '{term}' does not relax "
                f"maxDimensionM={_number_text(filters.max_dimension_m)}."
            )
        else:
            notes.append(
                f"Soft preference '{term}' is recorded only; it is not converted to a "
                "numeric or capability filter."
            )
    for token in numeric:
        notes.append(
            f"Query term '{token}' was not converted to a numeric filter; use the explicit "
            "min/max parameters in metres or bytes."
        )
    if filters.min_dimension_m is not None or filters.max_dimension_m is not None:
        notes.append("Dimension filters compare the largest bounding-box extent in metres.")
    return (
        keywords[:32],
        preferences[:8],
        [clip(note, 400) for note in notes[:10]],
        numeric[:16],
    )


def _exact_document(resource, identifier, ref=None):
    value = {"type": resource, "id": clip(identifier, 300)}
    if ref is not None:
        value["ref"] = clip(ref, 300)
    return value


def _exact_selector(text):
    value, prefix = text.strip(), None
    if value.startswith("catalog:"):
        value, prefix = value[8:], "catalog"
    elif value.startswith("asset:"):
        value, prefix = value[6:], "asset"
    catalog = CATALOG_ID.fullmatch(value)
    local = LOCAL_ID.fullmatch(value)
    if prefix == "catalog":
        return ("catalog", catalog.group("provider"), catalog.group("package"),
                catalog.group("revision")) if catalog else None
    if prefix == "asset":
        return ("local", local.group("kind"), local.group("name"),
                local.group("revision")) if local else None
    if catalog:
        return "catalog", catalog.group("provider"), catalog.group("package"), catalog.group("revision")
    if local:
        return "local", local.group("kind"), local.group("name"), local.group("revision")
    return None


def _entry_fields(entry):
    fields = [
        ("names.en", entry.names["en"], 100),
        ("names.zh", entry.names["zh"], 100),
    ]
    for language in ("en", "zh"):
        fields.extend(("aliases." + language, value, 80) for value in entry.aliases[language])
    for language in ("en", "zh"):
        fields.extend(("tags." + language, value, 60) for value in entry.tags[language])
    fields.extend([
        ("descriptions.en", entry.descriptions["en"], 40),
        ("descriptions.zh", entry.descriptions["zh"], 40),
        ("packageId", entry.package_id, 50),
        ("provider", entry.provider, 30),
        ("kind", entry.kind, 20),
    ])
    return fields


def _local_fields(local):
    fields = _entry_fields(local.entry) if local.entry is not None else []
    metadata = local.version.metadata
    fields.extend([
        ("id", local.version.id, 100),
        ("title", metadata.get("title", ""), 90),
        ("descriptions.en", metadata.get("description", ""), 40),
        ("kind", metadata.get("kind", ""), 20),
    ])
    return fields


def _match_fields(fields, keywords):
    if not keywords:
        return [], 0.0
    score, reasons = 0.0, []
    for keyword in keywords:
        found = next(((name, value, weight) for name, value, weight in fields
                      if value and _contains_keyword(value, keyword)), None)
        if found is None:
            return None, 0.0
        name, value, weight = found
        score += weight
        reasons.append({"field": name, "value": clip(value, 80), "query": keyword})
    normalized = " ".join(keyword.casefold() for keyword in keywords)
    if any(value and normalized == value.casefold() for name, value, _ in fields
           if name.startswith(("names", "title"))):
        score += 40
    unique = []
    seen = set()
    for reason in reasons:
        key = (reason["field"], reason["value"])
        if key not in seen:
            seen.add(key)
            unique.append(reason)
    return unique[:5], score


def _max_dimension(dimensions):
    if dimensions is None:
        return None
    return max(dimensions)


def _online_status(entry, local_versions, filters):
    status = {}
    if filters.kind is not None:
        status["kind"] = entry.kind == filters.kind
    if filters.provider is not None:
        status["provider"] = entry.provider == filters.provider
    if filters.usage is not None:
        status["usage"] = entry.usage_value(filters.usage)
    if filters.license_admitted is not None:
        status["licenseAdmitted"] = entry.license.admitted == filters.license_admitted
    if filters.installed is not None:
        status["installed"] = bool(local_versions) == filters.installed
    if filters.min_source_bytes is not None:
        status["minSourceBytes"] = entry.source.bytes >= filters.min_source_bytes
    if filters.max_source_bytes is not None:
        status["maxSourceBytes"] = entry.source.bytes <= filters.max_source_bytes
    size = _max_dimension(entry.adaptation.dimensions_m)
    if filters.min_dimension_m is not None:
        status["minDimensionM"] = None if size is None else size >= filters.min_dimension_m
    if filters.max_dimension_m is not None:
        status["maxDimensionM"] = None if size is None else size <= filters.max_dimension_m
    return status


def _local_status(local, filters):
    metadata = local.version.metadata
    usage = _local_usage(local)
    status = {}
    if filters.kind is not None:
        status["kind"] = metadata.get("kind") == filters.kind
    if filters.provider is not None:
        status["provider"] = local.entry.provider if local.entry is not None else None
    if filters.usage is not None:
        status["usage"] = usage[filters.usage]
    if filters.license_admitted is not None:
        license_document = _local_license(local)
        status["licenseAdmitted"] = (
            None if license_document["unknown"] else license_document["admitted"]
        )
    if filters.installed is not None:
        status["installed"] = filters.installed is True
    if filters.min_source_bytes is not None:
        status["minSourceBytes"] = (
            local.entry.source.bytes >= filters.min_source_bytes
            if local.entry is not None else None
        )
    if filters.max_source_bytes is not None:
        status["maxSourceBytes"] = (
            local.entry.source.bytes <= filters.max_source_bytes
            if local.entry is not None else None
        )
    size = _max_dimension(metadata.get("dimensionsM"))
    if filters.min_dimension_m is not None:
        status["minDimensionM"] = None if size is None else size >= filters.min_dimension_m
    if filters.max_dimension_m is not None:
        status["maxDimensionM"] = None if size is None else size <= filters.max_dimension_m
    return status


def _sort_key(match, sort):
    if match.type == "online":
        entry = match.entry
        title = entry.names["en"]
        source_bytes = entry.source.bytes
        identity = entry.id
    else:
        title = match.local.version.metadata.get("title", "")
        source_bytes = (
            match.local.entry.source.bytes if match.local.entry is not None else None
        )
        identity = match.local.version.id
    folded = title.casefold()
    if sort == "name":
        return (folded, identity)
    if sort == "sourceBytes":
        return (source_bytes is None, source_bytes if source_bytes is not None else 0, folded, identity)
    return (-match.score, folded, identity)


def _decode_cursor(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise LibraryError("invalid_cursor", "Cursor must be base64url JSON", 400)
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, binascii.Error, ValueError, TypeError, AttributeError):
        raise LibraryError("invalid_cursor", "Cursor must be base64url JSON", 400) from None
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or not isinstance(payload.get("key"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload["key"])
        or not isinstance(payload.get("offset"), int)
        or isinstance(payload.get("offset"), bool)
        or payload["offset"] < 0
        or not (payload.get("snapshot") is None or isinstance(payload.get("snapshot"), str))
    ):
        raise LibraryError("invalid_cursor", "Cursor payload is invalid", 400)
    return payload


def _encode_cursor(snapshot_id, key, offset):
    payload = json.dumps(
        {"v": 1, "snapshot": snapshot_id, "key": key, "offset": offset},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).rstrip(b"=").decode("ascii")


def _query_key(text, scope, filters, sort, approximate):
    value = {
        "scope": scope,
        "query": text,
        "filters": filters.applied(),
        "sort": sort,
        "approximate": bool(approximate),
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _usage_evidence(entry):
    values = []
    for name in ("dressing", "staticCollision"):
        text = entry.usage[name].evidence
        if text not in values:
            values.append(text)
    return " ".join(values)


def _license_document(license):
    return {
        "name": license.name,
        "admitted": license.admitted,
        "unknown": False,
        "obligations": list(license.obligations),
    }


def _local_asset(local):
    version = local.version
    metadata = version.metadata
    summary = {}
    try:
        summary = version.summary()
    except (OSError, ValueError, LibraryError):
        summary = {}
    gaps = summary.get("gaps") or []
    return {
        "id": version.id,
        "title": metadata.get("title"),
        "kind": metadata.get("kind"),
        "dimensionsM": metadata.get("dimensionsM"),
        "implementedTypes": summary.get("implementedTypes") or [],
        "gaps": gaps,
        "gapCount": summary.get("gapCount", len(gaps)),
        "completeness": summary.get("completeness") or {},
        "validationStatus": summary.get("validationStatus"),
        "thumbnailUrl": summary.get("thumbnailUrl"),
        "visualUrl": summary.get("visualUrl"),
    }


def _local_preview_document(local):
    preview = preview_document([local])
    if preview["kind"] == "local":
        return preview
    return {
        "kind": "unavailable",
        "url": None,
        "reason": "The local package has no recorded preview image.",
    }


def _candidate(match, filters, language, correlated, by_bare_id):
    satisfied, unsatisfied, unknown = match.lists()
    if match.type == "online":
        entry = match.entry
        locals_ = correlated.get(entry.id, [])
        return {
            "type": "online",
            "ref": entry.ref,
            "id": entry.id,
            "kind": entry.kind,
            "title": entry.names[language],
            "names": dict(entry.names),
            "descriptions": dict(entry.descriptions),
            "dimensionsM": (
                list(entry.adaptation.dimensions_m)
                if entry.adaptation.dimensions_m is not None else None
            ),
            "source": {
                "provider": entry.provider,
                "packageId": entry.package_id,
                "revision": entry.revision,
                "bytes": entry.source.bytes,
                "files": entry.source.files,
            },
            "license": _license_document(entry.license),
            "usage": {
                "dressing": entry.usage_value("dressing"),
                "staticCollision": entry.usage_value("staticCollision"),
                "evidence": _usage_evidence(entry),
            },
            "installed": installed_document(locals_, limit=3),
            "acquisition": entry.acquisition.document(),
            "match": {
                "reasons": match.reasons,
                "satisfied": satisfied,
                "unsatisfied": unsatisfied,
                "unknown": unknown,
            },
            "unknown": entry.unknown_attributes(),
            "detail": {"resource": "catalog", "id": entry.id, "tool": "catalog_show"},
            "preview": preview_document(locals_),
        }
    local = match.local
    version = local.version
    names = {
        "en": version.metadata.get("title"),
        "zh": local.entry.names["zh"] if local.entry is not None else None,
    }
    descriptions = {
        "en": version.metadata.get("description", ""),
        "zh": local.entry.descriptions["zh"] if local.entry is not None else None,
    }
    own = by_bare_id.get(version.bare_id, [])
    installed = installed_document(own or [local], limit=3)
    return {
        "type": "local",
        "ref": f"asset:{version.id}",
        "id": version.id,
        "kind": version.metadata.get("kind"),
        "title": names[language] if names[language] else names["en"],
        "names": names,
        "descriptions": descriptions,
        "dimensionsM": version.metadata.get("dimensionsM"),
        "source": _local_source(local),
        "license": _local_license(local),
        "usage": _local_usage(local),
        "installed": installed,
        "acquisition": {
            "eligible": False,
            "reasons": ["Already installed as a local asset; acquisition is not applicable."],
        },
        "match": {
            "reasons": match.reasons,
            "satisfied": satisfied,
            "unsatisfied": unsatisfied,
            "unknown": unknown,
        },
        "unknown": _local_unknown(local),
        "detail": {"resource": "asset", "id": version.id, "tool": "library_describe"},
        "preview": _local_preview_document(local),
        "local": {
            "id": version.id,
            "rev": version.rev,
            "root": version.root,
            "contentHash": version.content_hash,
            "bytes": package_bytes(version),
            "asset": _local_asset(local),
        },
    }


def _snapshot_document(catalog, now):
    if catalog.available and catalog.snapshot is not None:
        return catalog.snapshot.document(now)
    return unavailable_snapshot_document()


def _summary_response(scope, interpreted, filters, snapshot, counts, results, next_cursor,
                      truncated, zero):
    applied = {
        name: clip(value, 200) if isinstance(value, str) else value
        for name, value in filters.applied().items()
    }
    return {
        "schema": SEARCH_SCHEMA,
        "scope": scope,
        "interpretedQuery": interpreted,
        "appliedFilters": applied,
        "unknownPolicy": UNKNOWN_POLICY,
        "snapshot": snapshot,
        "counts": counts,
        "results": results,
        "nextCursor": next_cursor,
        "truncated": truncated,
        "zeroResult": zero,
    }


def _fits(response):
    return serialized_size(response) <= MAX_BYTES


def _shrink(response):
    """Last-resort identity trimming for adversarial query text or coverage."""
    response["counts"]["budgetTruncated"] = True
    for _ in range(1000):
        if serialized_size(response) <= MAX_BYTES:
            return
        if response["interpretedQuery"]["notes"]:
            response["interpretedQuery"]["notes"].pop()
        elif response["interpretedQuery"]["keywords"]:
            response["interpretedQuery"]["keywords"].pop()
        elif response["interpretedQuery"].get("preferences"):
            response["interpretedQuery"]["preferences"].pop()
        elif len(response["interpretedQuery"]["text"]) > 80:
            response["interpretedQuery"]["text"] = clip(
                response["interpretedQuery"]["text"],
                max(80, len(response["interpretedQuery"]["text"]) // 2),
            )
        elif response["interpretedQuery"].get("exact"):
            exact = response["interpretedQuery"]["exact"]
            if len(exact.get("ref") or "") > 80:
                exact["ref"] = clip(exact["ref"], 80)
            elif len(exact.get("id") or "") > 80:
                exact["id"] = clip(exact["id"], 80)
            else:
                response["interpretedQuery"]["exact"] = None
        elif response["snapshot"]["coverage"]:
            coverage = response["snapshot"]["coverage"]
            response["snapshot"]["coverage"] = clip(coverage, 200) if len(coverage) > 200 else ""
        elif response["zeroResult"] and response["zeroResult"]["messages"]:
            response["zeroResult"]["messages"].pop()
        elif response["zeroResult"] and response["zeroResult"]["suggestions"]:
            response["zeroResult"]["suggestions"].pop()
        else:
            break


def _zero(reason, messages, excluded_by, suggestions):
    return {
        "reason": reason,
        "messages": [clip(message, 400) for message in messages[:10]],
        "excludedBy": excluded_by[:9],
        "suggestions": suggestions[:11],
    }


def _zero_document(scope, interpreted, filters, snapshot, reason, messages, excluded_by,
                   suggestions):
    document = _summary_response(
        scope, interpreted, filters, snapshot,
        {"returned": 0, "matching": 0, "localMatching": 0, "onlineMatching": 0,
         "approximate": 0, "budgetTruncated": False},
        [], None, False, _zero(reason, messages, excluded_by, suggestions),
    )
    if not _fits(document):
        _shrink(document)
    if not _fits(document):
        document["interpretedQuery"] = {
            "text": clip(document["interpretedQuery"].get("text", ""), 80),
            "keywords": [],
            "preferences": [],
            "exact": None,
            "approximateRequested": bool(
                document["interpretedQuery"].get("approximateRequested")
            ),
            "notes": [],
        }
        document["snapshot"]["coverage"] = clip(document["snapshot"]["coverage"], 200)
        document["zeroResult"] = {
            "reason": reason,
            "messages": [],
            "excludedBy": [],
            "suggestions": [],
        }
        document["counts"]["budgetTruncated"] = True
    if reason == "response_budget":
        document["counts"]["budgetTruncated"] = True
    return document


def _other_scope_matches(scope, online_matches, local_matches):
    if scope == "local":
        return bool(online_matches)
    if scope == "online":
        return bool(local_matches)
    return False


def search_catalog(
    catalog,
    library,
    *,
    q=None,
    scope="all",
    kind=None,
    provider=None,
    usage=None,
    license_admitted=None,
    installed=None,
    min_source_bytes=None,
    max_source_bytes=None,
    min_dimension_m=None,
    max_dimension_m=None,
    approximate=False,
    sort="relevance",
    limit=DEFAULT_LIMIT,
    cursor=None,
    now=None,
):
    if scope not in SCOPES:
        raise ValueError("scope must be all, local or online")
    if sort not in SORTS:
        raise ValueError("sort must be relevance, name or sourceBytes")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise LibraryError("invalid_limit", f"limit must be an integer from 1 to {MAX_LIMIT}", 400)
    for name, value in (("minDimensionM", min_dimension_m), ("maxDimensionM", max_dimension_m)):
        if value is not None and not math.isfinite(value):
            raise LibraryError("invalid_filter", f"{name} must be a finite number", 400)
    filters = Filters(
        kind=kind,
        provider=provider,
        usage=usage,
        license_admitted=license_admitted,
        installed=installed,
        min_source_bytes=min_source_bytes,
        max_source_bytes=max_source_bytes,
        min_dimension_m=min_dimension_m,
        max_dimension_m=max_dimension_m,
    )
    if scope in ("all", "online"):
        catalog.require()
    now = _as_utc(now)
    text = (q or "").strip()
    language = "zh" if _cjk(text) else "en"
    keywords, preferences, notes, numeric_terms = _interpret(text, filters)
    exact = _exact_selector(text) if text else None
    if exact is not None:
        keywords = [clip(text, 80)]
    if scope == "local" and catalog.available and not catalog.entries:
        notes.append("The catalog snapshot is empty; local results are unaffected.")
    if scope == "local" and not catalog.available:
        notes.insert(0, "The catalog is unavailable; only local results are available.")
    snapshot = _snapshot_document(catalog, now)
    snapshot_id = snapshot["id"]
    interpreted = {
        "text": clip(text, 500),
        "keywords": [clip(keyword, 80) for keyword in keywords[:16]],
        "preferences": preferences,
        "exact": None,
        "approximateRequested": bool(approximate),
        "notes": notes,
    }
    key = _query_key(text, scope, filters, sort, approximate)
    offset = 0
    if cursor is not None:
        payload = _decode_cursor(cursor)
        if payload["key"] != key:
            raise LibraryError("cursor_mismatch", "Cursor belongs to a different query", 400)
        if payload["snapshot"] != snapshot_id:
            raise LibraryError("stale_cursor", "The catalog snapshot changed; search again", 400)
        offset = payload["offset"]
    if text and not keywords and not preferences and numeric_terms:
        return _zero_document(
            scope, interpreted, filters, snapshot, "no_query_match",
            ["The query contains only number+unit terms; no keyword remains after the "
             "correcting note. Use the explicit min/max parameters instead."],
            [], [],
        )

    locals_all = catalog.local_assets(library) if library is not None else []
    correlated = {}
    for item in locals_all:
        if item.entry is not None:
            correlated.setdefault(item.entry.id, []).append(item)
    by_bare_id = {}
    for item in locals_all:
        by_bare_id.setdefault(item.version.bare_id, []).append(item)

    if exact is not None:
        kind_, first, second, third = exact
        if kind_ == "catalog":
            entry = catalog.by_package.get(second) if catalog.available else None
            if entry is not None and (entry.provider != first or entry.revision != third):
                entry = None
            if scope == "local":
                if entry is None:
                    interpreted["exact"] = _exact_document("catalog", f"{first}/{second}@{third}")
                    return _zero_document(
                        scope, interpreted, filters, snapshot, "unknown_exact_id",
                        [f"No catalog entry has the exact ID '{clip(text, 200)}'."], [], [],
                    )
                interpreted["exact"] = _exact_document("catalog", entry.id)
                return _zero_document(
                    scope, interpreted, filters, snapshot, "scope",
                    ["The exact catalog identity is outside scope=local."],
                    [], [{"action": "broaden_scope"}],
                )
            if entry is None:
                interpreted["exact"] = _exact_document("catalog", f"{first}/{second}@{third}")
                return _zero_document(
                    scope, interpreted, filters, snapshot, "unknown_exact_id",
                    [f"No catalog entry has the exact ID '{clip(text, 200)}'."], [], [],
                )
            interpreted["exact"] = _exact_document("catalog", entry.id, entry.ref)
            local_versions = correlated.get(entry.id, [])
            match = _Match(
                "online", entry, None, [{"field": "id", "value": entry.id, "query": text}],
                1000.0, _online_status(entry, local_versions, filters),
            )
        else:
            try:
                version = library.resolve(
                    f"{first}/{second}" + (f"@{third}" if third is not None else "")
                ) if library is not None else None
            except LibraryError:
                version = None
            if version is None:
                interpreted["exact"] = _exact_document(
                    "local", f"{first}/{second}" + (f"@{third}" if third else "")
                )
                return _zero_document(
                    scope, interpreted, filters, snapshot, "unknown_exact_id",
                    [f"No local asset has the exact ID '{clip(text, 200)}'."], [], [],
                )
            interpreted["exact"] = _exact_document("local", version.id)
            if scope == "online":
                return _zero_document(
                    scope, interpreted, filters, snapshot, "scope",
                    ["The exact local identity is outside scope=online."],
                    [], [{"action": "broaden_scope"}],
                )
            local = next(
                (item for item in locals_all if item.version.id == version.id),
                LocalAsset(version, None, None),
            )
            match = _Match(
                "local", None, local, [{"field": "id", "value": version.id, "query": text}],
                1000.0, _local_status(local, filters),
            )
        if any(value is not True for value in match.status.values()):
            notes.append(
                "Exact ID conditions are not relaxed by approximate results; remove the "
                "failing filters."
            )
            failing = [name for name, value in match.status.items() if value is not True]
            return _zero_document(
                scope, interpreted, filters, snapshot, "hard_filters",
                [f"Exact ID '{match.entry.id if match.type == 'online' else match.local.version.id}' "
                 "matched but a hard filter excluded it; unknown attributes never satisfy a filter."],
                [{"filter": name, "count": 1} for name in failing],
                [{"action": "remove_filter", "filter": name} for name in failing],
            )
        results = [compact_candidate(_candidate(match, filters, language, correlated, by_bare_id))]
        response = _summary_response(
            scope, interpreted, filters, snapshot,
            {"returned": 1, "matching": 1,
             "localMatching": 1 if match.type == "local" else 0,
             "onlineMatching": 1 if match.type == "online" else 0,
             "approximate": 0, "budgetTruncated": False},
            results, None, False, None,
        )
        if not _fits(response):
            identity = clip(
                match.entry.id if match.type == "online" else match.local.version.id, 200
            )
            return _zero_document(
                scope, interpreted, filters, snapshot, "response_budget",
                [f"The exact candidate '{identity}' exceeds the 32 KiB compact budget; read "
                 "it through its library or catalog detail."],
                [], [],
            )
        return response

    online_matches = []
    if scope in ("all", "online"):
        for entry in catalog.entries:
            reasons, score = _match_fields(_entry_fields(entry), keywords)
            if reasons is not None:
                local_versions = correlated.get(entry.id, [])
                online_matches.append(_Match(
                    "online", entry, None, reasons, score,
                    _online_status(entry, local_versions, filters),
                ))
    local_matches = []
    if scope in ("local", "all"):
        for local in locals_all:
            if scope == "all" and local.entry is not None:
                continue
            reasons, score = _match_fields(_local_fields(local), keywords)
            if reasons is not None:
                local_matches.append(_Match(
                    "local", None, local, reasons, score, _local_status(local, filters),
                ))
    scope_matches = online_matches + local_matches
    exact_matches = [item for item in scope_matches
                     if all(value is True for value in item.status.values())]
    approximate_matches = []
    if approximate:
        approximate_matches = [item for item in scope_matches
                               if any(value is not True for value in item.status.values())]
        if approximate_matches:
            notes.append(
                f"{len(approximate_matches)} approximate result(s) do not satisfy every hard "
                "filter; unknown attributes are listed per candidate."
            )
    exact_matches.sort(key=lambda item: _sort_key(item, sort))
    approximate_matches.sort(
        key=lambda item: (
            -sum(value is True for value in item.status.values()),
            *_sort_key(item, sort),
        )
    )
    ordered = exact_matches + approximate_matches
    local_count = sum(item.type == "local" for item in ordered)
    online_count = sum(item.type == "online" for item in ordered)
    excluded_by = []
    for name in filters.names():
        count = sum(item.status.get(name) is not True for item in scope_matches)
        if count:
            excluded_by.append({"filter": name, "count": count})
    if not ordered:
        other = _other_scope_matches(
            scope,
            [entry for entry in (catalog.entries if catalog.available else [])
             if _match_fields(_entry_fields(entry), keywords)[0] is not None],
            [item for item in locals_all
             if _match_fields(_local_fields(item), keywords)[0] is not None],
        )

        if scope == "online" and catalog.available and not catalog.entries:
            reason = "empty_catalog"
            messages = ["The catalog snapshot contains no entries."]
            suggestions = []
        elif scope == "all" and catalog.available and not catalog.entries and not locals_all:
            reason = "empty_catalog"
            messages = ["The catalog snapshot contains no entries and the local library is empty."]
            suggestions = []
        elif excluded_by:
            reason = "hard_filters"
            messages = [
                "Candidates matched the query text but not every hard filter; unknown "
                "attributes never satisfy a filter.",
                *(f"Filter '{item['filter']}' excluded {item['count']} candidate(s)."
                  for item in excluded_by),
            ]
            suggestions = [
                {"action": "remove_filter", "filter": item["filter"]} for item in excluded_by
            ]
            if other:
                suggestions.append({"action": "broaden_scope"})
            if not approximate:
                suggestions.append({"action": "enable_approximation"})
        elif other:
            reason = "scope"
            messages = [f"No {scope} results matched; matching candidates exist in another scope."]
            suggestions = [{"action": "broaden_scope"}]
        else:
            reason = "no_query_match"
            messages = [
                "No candidate matches the query tokens within the requested scope."
                if keywords else "The requested scope contains no candidates."
            ]
            suggestions = []
        return _zero_document(scope, interpreted, filters, snapshot, reason, messages,
                              excluded_by, suggestions)

    if offset >= len(ordered):
        raise LibraryError(
            "invalid_cursor",
            "Cursor offset is past the matching candidates; repeat the search",
            400,
        )
    page = ordered[offset: offset + limit]
    results = [
        compact_candidate(_candidate(item, filters, language, correlated, by_bare_id))
        for item in page
    ]
    budget = False

    def build(values):
        next_offset = offset + len(values)
        has_more = next_offset < len(ordered)
        return _summary_response(
            scope, interpreted, filters, snapshot,
            {"returned": len(values), "matching": len(ordered), "localMatching": local_count,
             "onlineMatching": online_count,
             "approximate": len(approximate_matches), "budgetTruncated": budget},
            values,
            _encode_cursor(snapshot_id, key, next_offset) if has_more else None,
            has_more,
            None,
        )

    response = build(results)
    while not _fits(response) and results:
        budget = True
        results.pop()
        response = build(results)
    if not results:
        return _zero_document(
            scope, interpreted, filters, snapshot, "response_budget",
            ["The matching page exceeds the 32 KiB compact budget even after trimming; "
             "narrow the query or request a single candidate by exact ID."],
            [], [],
        )
    if not _fits(response):
        _shrink(response)
        if not _fits(response):
            return _zero_document(
                scope, interpreted, filters, snapshot, "response_budget",
                ["The matching page exceeds the 32 KiB compact budget even after trimming; "
                 "narrow the query or request a single candidate by exact ID."],
                [], [],
            )
    return response
