"""Discovery is bounded and pages preserve every byte of the complete description."""

import hashlib
import json
from typing import Any

import pytest
from mcp.server.mcpserver.tools.base import Tool

from packages.library.common import LibraryError
from packages.library.description_views import MAX_BYTES, canonical_json, description_view, serialized_size


def document():
    return {"id": "example@2", "title": "例子", "contentHash": "a" * 64,
            "entityCount": None, "deviceCount": None, "countsReason": "Not physically inspected",
            "devices": [{"key": "/World/Arm", "type": "arm/v1"}],
            "types": {"arm/v1": {"actions": {"move": {"schema": "large"}}}},
            "locations": {"slot1": {"pose": [1, 2, 3]}}, "gaps": ["No physics report"],
            "recordings": [], "large": ('中文😀"\\\n\u0001' * 4000), "empty": []}


def test_summary_preserves_unknown_counts_and_representative_discovery():
    full = document()
    summary = description_view(full, "scene", "example", view="summary")
    assert summary["counts"]["entities"] is None
    assert summary["counts"]["devices"] is None
    assert summary["countsReason"] == "Not physically inspected"
    assert summary["devicesPreview"] == [{"entityKey": "/World/Arm", "type": "arm/v1"}]
    assert summary["typesPreview"] == [{"type": "arm/v1", "actions": ["move"]}]
    assert summary["gapsPreview"] == ["No physics report"]
    assert serialized_size(summary) <= MAX_BYTES
    assert description_view(full, "scene", "example") is full


@pytest.mark.parametrize("section", [None, "large", "empty"])
def test_pages_reconstruct_canonical_json_including_unicode_and_large_leaf(section):
    full = document()
    summary = description_view(full, "scene", "example@2", view="summary")
    expected = canonical_json(full if section is None else full[section])
    assert summary["documentDigest"] == hashlib.sha256(canonical_json(full).encode()).hexdigest()
    offset, pieces = 0, []
    while True:
        page = description_view(full, "scene", "example@2", view="page", section=section,
                                offset=offset, document_digest=summary["documentDigest"])
        assert serialized_size(page) <= MAX_BYTES
        assert page["offset"] == offset and page["totalChars"] == len(expected)
        pieces.append(page["content"])
        if page["nextOffset"] is None:
            break
        assert page["nextOffset"] > offset
        offset = page["nextOffset"]
    assert "".join(pieces) == expected
    assert json.loads(expected) == (full if section is None else full[section])
    last = description_view(full, "scene", "example@2", view="page", section=section,
                            offset=len(expected), document_digest=summary["documentDigest"])
    assert last["content"] == "" and last["nextOffset"] is None


@pytest.mark.parametrize("change", ["recordings", "validation", "referenceInspection"])
def test_digest_pins_the_whole_description_including_mutable_metadata(change):
    full = document()
    digest = description_view(full, "scene", "example", view="summary")["documentDigest"]
    full[change] = {"new": True}
    with pytest.raises(LibraryError) as caught:
        description_view(full, "scene", "example@2", view="page", document_digest=digest)
    assert caught.value.status == 409
    assert caught.value.detail["code"] == "description_changed"


@pytest.mark.parametrize("selector,options", [
    ("example", {"view": "page"}), ("example@2", {"view": "page"}),
    ("example@2", {"view": "page", "section": "missing"}),
    ("example@2", {"view": "page", "offset": -1}),
    ("example@2", {"view": "page", "offset": 999999}),
    ("example@2", {"view": "wrong"}), ("example@2", {"view": "summary", "section": "types"}),
])
def test_invalid_view_requests_have_bounded_errors(selector, options):
    full = document()
    if options.get("view") == "page" and options != {"view": "page"}:
        summary = description_view(full, "scene", selector, view="summary")
        options["document_digest"] = summary["documentDigest"]
    with pytest.raises(LibraryError) as caught:
        description_view(full, "scene", selector, **options)
    assert caught.value.status == 400
    assert caught.value.detail["code"] == "invalid_description_view"
    assert serialized_size(caught.value.detail) < MAX_BYTES


def test_oversized_previews_are_explicit_and_actionable_identifiers_never_shorten():
    full = document()
    full["types"] = {"type" + "名" * 4000: {"actions": {"move": {}}}}
    full["gaps"] = ["failure" * 8000]
    full["launchReason"] = "Cannot launch: " + "failure" * 8000
    summary = description_view(full, "scene", "example", view="summary")
    assert serialized_size(summary) <= MAX_BYTES
    assert summary["previewTruncated"] is True
    assert summary["typesPreview"] == []
    assert summary["launchReason"].startswith("Cannot launch:")
    assert summary["details"]["view"] == "page"


def test_oversized_identity_cannot_bypass_budget_with_an_empty_final_page():
    full = document() | {"id": "a" * 5000 + "@2"}
    digest = hashlib.sha256(canonical_json(full).encode()).hexdigest()
    with pytest.raises(LibraryError) as caught:
        description_view(full, "scene", full["id"], view="page", section="empty", offset=2,
                         document_digest=digest)
    assert caught.value.status == 400
    assert serialized_size(caught.value.detail) < MAX_BYTES


@pytest.mark.parametrize("text", ["😀", "\u0001", '"\\\n'])
def test_official_sdk_full_result_budget_for_adversarial_unicode_and_escaping(text):
    def output() -> dict[str, Any]:
        return {}

    converter = Tool.from_function(output).fn_metadata
    full = document() | {"large": text * 6000}
    summary = description_view(full, "scene", full["id"], view="summary")
    assert len(converter.convert_result(summary).model_dump_json(by_alias=True).encode()) <= 16384
    offset = 0
    while True:
        page = description_view(full, "scene", full["id"], view="page", section="large", offset=offset,
                                document_digest=summary["documentDigest"])
        wire = converter.convert_result(page).model_dump_json(by_alias=True).encode()
        assert len(wire) <= 16384
        if page["nextOffset"] is None:
            break
        offset = page["nextOffset"]
