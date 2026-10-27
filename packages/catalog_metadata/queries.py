"""Bounded catalog-native reads; admission and installed Assets belong to consumers."""

import base64
import json

from .models import MetadataError, canonical, digest


def _clip(value, limit):
    return value[:limit] if isinstance(value, str) else value


def describe(store, identity, *, release=None, section=None, offset=0, document_digest=None):
    selected = store.release(release)
    entry = store.entry(identity.removeprefix("catalog:"), release_id=selected["id"])
    fingerprint = digest(entry)
    if document_digest is not None and document_digest != fingerprint:
        raise MetadataError("catalog_changed", "Read a fresh entry after its document changed", 409)
    sections = [
        key for key in entry if key not in ("id", "collectionId", "upstreamId", "revision", "inputDigest")
    ]
    result = {
        "schema": "lw.catalog-native-entry/1",
        "id": entry["id"],
        "release": selected["id"],
        "documentDigest": fingerprint,
    }
    if section is not None:
        if section not in sections or type(offset) is not int or offset < 0:
            raise MetadataError("invalid_section", "Select an available section and nonnegative byte offset")
        raw = canonical(entry[section]).encode("utf-8")
        if offset > len(raw):
            raise MetadataError("invalid_offset", "Section offset exceeds its byte length")
        chunk = raw[offset : offset + 8192]
        try:
            content, encoding = chunk.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            content, encoding = base64.b64encode(chunk).decode("ascii"), "base64"
        return result | {
            "view": "section",
            "section": section,
            "offset": offset,
            "bytes": len(raw),
            "content": content,
            "encoding": encoding,
            "nextOffset": offset + len(chunk) if offset + len(chunk) < len(raw) else None,
        }
    if offset:
        raise MetadataError("invalid_offset", "Offsets apply to a selected section")
    result.update(view="summary", collectionId=entry["collectionId"], revision=entry["revision"])
    for field in ("name", "summary"):
        result[field] = {
            lang: _clip(value, 400 if field == "summary" else 160)
            for lang, value in entry.get(field, {}).items()
        }
    result["aliases"] = {
        lang: [_clip(value, 100) for value in values[:4]] for lang, values in entry.get("aliases", {}).items()
    }
    for field in ("tags", "domains", "uses"):
        result[field] = [_clip(value, 100) for value in entry.get(field, [])[:6]]
    result["category"] = entry.get("category")
    result["organization"] = {key: entry.get("organization", {}).get(key) for key in ("status", "version")}
    result["preview"] = entry.get("preview", {}).get("thumbnail") or {
        "state": entry.get("preview", {}).get("state", "not_collected")
    }
    facts = entry.get("facts", {})
    result["facts"] = {
        key: value
        for key, value in facts.items()
        if key
        in (
            "formats",
            "dimensionsM",
            "unit",
            "coordinateSystem",
            "parts",
            "joints",
            "sourceBytes",
            "fileCount",
            "license",
        )
        and len(canonical(value).encode()) <= 512
    }
    result["sections"] = sections
    while len(canonical(result).encode()) > 8192:
        for field in ("aliases", "facts", "tags", "domains", "uses"):
            if result.get(field):
                result[field] = {} if isinstance(result[field], dict) else []
                result["summaryTruncated"] = True
                break
        else:
            raise MetadataError("metadata_response_limit", "Entry identity exceeds the summary budget", 413)
    return result


def search(store, *, q="", collection=None, category=None, tag=None, release=None, limit=10, cursor=None):
    exact = q.removeprefix("catalog:")
    exact_query = "@" in exact and "/" in exact
    if (
        type(limit) is not int
        or not 1 <= limit <= 50
        or len(q) > (8192 if exact_query else 512)
        or any(value is not None and len(value) > 160 for value in (collection, category, tag))
    ):
        raise MetadataError("invalid_query", "Use bounded query/filter text and limit 1..50")
    filters = {"q": q, "collection": collection, "category": category, "tag": tag}
    offset = 0
    if cursor:
        try:
            if len(cursor) > 8192:
                raise ValueError()
            saved = json.loads(base64.urlsafe_b64decode(cursor))
            if (
                saved["filters"] != filters
                or type(saved["offset"]) is not int
                or saved["offset"] < 0
                or (release is not None and release != saved["release"])
            ):
                raise ValueError()
            release, offset = saved["release"], saved["offset"]
        except (ValueError, KeyError, TypeError) as exc:
            raise MetadataError(
                "cursor_mismatch", "Reuse the cursor with its original query and filters"
            ) from exc
    selected = store.release(release)
    where, parameters = ["release_id=?"], [selected["id"]]
    for key, value in (("collectionId", collection), ("category", category)):
        if value is not None:
            where.append("json_extract(document, '$." + key + "')=?")
            parameters.append(value)
    if tag is not None:
        where.append("EXISTS (SELECT 1 FROM json_each(json_extract(document,'$.tags')) WHERE value=?)")
        parameters.append(tag)
    if exact_query:
        where.append("id=?")
        parameters.append(exact)
    else:
        for word in q.casefold().split():
            where.append(
                "EXISTS (SELECT 1 FROM json_tree(search_text) WHERE type='text' AND instr(lower(value),?)>0)"
            )
            parameters.append(word)
    condition = " AND ".join(where)
    with store.transaction(write=False) as db:
        total = db.execute("SELECT COUNT(*) FROM release_entries WHERE " + condition, parameters).fetchone()[
            0
        ]
        rows = db.execute(
            "SELECT id FROM release_entries WHERE " + condition + " ORDER BY id LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        ).fetchall()
    result = {
        "schema": "lw.catalog-native-search/1",
        "release": selected["id"],
        "filters": filters,
        "total": total,
        "results": [],
        "nextCursor": None,
    }
    for row in rows:
        entry = describe(store, row[0], release=selected["id"])
        if (
            result["results"]
            and len(canonical(result | {"results": result["results"] + [entry]}).encode()) > 30000
        ):
            break
        result["results"].append(entry)
    following = offset + len(result["results"])
    while True:
        result["nextCursor"] = (
            base64.urlsafe_b64encode(
                canonical(
                    {
                        "release": selected["id"],
                        "filters": filters,
                        "offset": following,
                    }
                ).encode()
            ).decode("ascii")
            if following < total
            else None
        )
        if len(canonical(result).encode()) <= 32768:
            break
        result["results"].pop()
        following -= 1
    return result
