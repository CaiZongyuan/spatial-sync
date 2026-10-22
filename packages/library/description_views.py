"""Bounded discovery views of an unchanged, complete library description."""

import hashlib
import json
import re

from packages.library.common import LibraryError

SCHEMA = "lw.library-description/1"
MAX_BYTES = 4096


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def invalid_view(message):
    return LibraryError("invalid_description_view", message, 400)


def serialized_size(value):
    # Covers the installed CLI's ordinary JSON spaces and Windows CRLF.
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 2


def description_view(detail, resource, selector, *, view="full", section=None, offset=0,
                     document_digest=None):
    """Hash the whole document, including mutable report/reference/recording state."""
    # HTTP supplies text so malformed offsets use this bounded error, not FastAPI's input echo.
    if isinstance(offset, str):
        if not re.fullmatch(r"[+-]?[0-9]+", offset):
            raise invalid_view("offset must be an integer")
        try:
            offset = int(offset)
        except ValueError:
            raise invalid_view("offset must be an integer") from None
    if view not in ("summary", "page", "full"):
        raise invalid_view("view must be summary, page or full")
    if view != "page" and (section is not None or offset != 0 or document_digest is not None):
        raise invalid_view("section, offset and documentDigest require view=page")
    if view == "full":
        return detail
    document = canonical_json(detail)
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    if view == "summary":
        return _summary(detail, resource, digest)
    if not re.search(r"@[1-9][0-9]*$", selector) or document_digest is None:
        raise invalid_view("page requires an exact @rev and documentDigest from summary")
    if document_digest != digest:
        raise LibraryError("description_changed", "Fetch a fresh summary and restart pages when metadata "
                           "is stable; the complete description changed", 409)
    if section is not None and section not in detail:
        raise invalid_view("section must name an existing top-level field")
    content = document if section is None else canonical_json(detail[section])
    if offset < 0 or offset > len(content):
        raise invalid_view("offset must be between zero and totalChars")
    page = {"schema": SCHEMA, "view": "page", "resource": resource, "id": detail["id"],
            "documentDigest": digest, "section": section, "offset": offset,
            "nextOffset": None, "totalChars": len(content), "content": content[offset:]}
    # Envelope escaping and UTF-8, not the raw substring length, set the budget.
    if serialized_size(page) <= MAX_BYTES:
        return page
    lower, upper = offset, len(content) - 1
    while lower < upper:
        end = (lower + upper + 1) // 2
        page.update(content=content[offset:end], nextOffset=end)
        if serialized_size(page) <= MAX_BYTES:
            lower = end
        else:
            upper = end - 1
    page.update(content=content[offset:lower], nextOffset=lower if lower < len(content) else None)
    if serialized_size(page) > MAX_BYTES or (lower == offset and offset < len(content)):
        raise invalid_view("page identity exceeds the description budget")
    return page


def _summary(detail, resource, digest):
    report = detail.get("validation") or {}
    gaps = detail.get("gaps", [])
    types = detail.get("types", {})
    summary = {
        "schema": SCHEMA, "view": "summary", "resource": resource, "id": detail["id"],
        "title": detail.get("title"), "description": detail.get("description"),
        "contentHash": detail["contentHash"], "documentDigest": digest,
        "launchable": detail.get("launchable"), "sources": detail.get("sources"),
        "launchReason": detail.get("launchReason"),
        "countsReason": (detail.get("countsReason") if resource == "scene"
                         else "Physical counts apply to scenes"),
        "validation": {"status": detail.get("validationStatus"), "reportId": report.get("reportId"),
                       "gapCount": detail.get("gapCount", len(gaps))},
        "counts": {"entities": detail.get("entityCount"), "devices": detail.get("deviceCount"),
                   "types": len(types), "locations": len(detail.get("locations", {})),
                   "assets": len(detail.get("assets", [])), "files": len(detail.get("files", {}))},
        "typesPreview": [{"type": name, "actions": sorted((definition or {}).get("actions", {}))}
                         for name, definition in sorted(types.items())],
        "devicesPreview": [], "locationsPreview": sorted(detail.get("locations", {})),
        "gapsPreview": gaps.copy(), "sections": sorted(detail), "previewTruncated": False,
        "details": {"view": "page", "id": detail["id"], "documentDigest": digest,
                    "section": None, "offset": 0},
    }
    # Keep one representative complete entity key per type for initial discovery.
    seen = set()
    for device in detail.get("devices", []):
        if device.get("type") not in seen:
            summary["devicesPreview"].append({"entityKey": device.get("entityKey") or device["key"],
                                              "type": device.get("type")})
            seen.add(device.get("type"))
    summary["previewTruncated"] = len(summary["devicesPreview"]) < len(detail.get("devices", []))
    # Reasons remain discoverable even when their complete text needs a page.
    for field in ("title", "description", "launchReason", "countsReason"):
        value = summary[field]
        if isinstance(value, str) and len(value) > 160:
            summary[field] = value[:160] + "…"
            summary["previewTruncated"] = True
    while serialized_size(summary) > MAX_BYTES:
        previews = [summary[field] for field in ("devicesPreview", "typesPreview", "locationsPreview",
                                                "gapsPreview", "sections") if summary[field]]
        if not previews:
            raise invalid_view("summary identity exceeds the description budget")
        max(previews, key=serialized_size).pop()
        summary["previewTruncated"] = True
    return summary
