"""Small JSON contracts shared by the input reader, storage and consumers."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import quote

SCHEMA = "lw.catalog-metadata/1"
VOCABULARY_VERSION = "lw.catalog-vocabulary/1"
RECIPE_VERSION = "lw.hf-recipes/1"
REVISION = re.compile(r"[0-9a-f]{40}")
IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,79}")
DATASET = re.compile(r"[A-Za-z0-9][\w.-]{0,95}/[A-Za-z0-9][\w.-]{0,95}")
ORGANIZED_FIELDS = frozenset(("name", "aliases", "summary", "category", "tags", "domains", "uses"))


class MetadataError(ValueError):
    def __init__(self, code, message, status=400, **details):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def now():
    return datetime.now(UTC).isoformat()


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MetadataError("invalid_metadata", "Metadata must be finite JSON") from exc


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, name, maximum=8192, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not value.strip() and not empty):
        raise MetadataError("invalid_metadata", f"{name} must be text of at most {maximum} characters")
    return value.strip()


def revision(value):
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise MetadataError("invalid_revision", "A fixed 40-character dataset revision is required")
    return value


def source_profile(value):
    if not isinstance(value, dict):
        raise MetadataError("invalid_collection", "A collection profile must be an object")
    result = deepcopy(value)
    identifier, dataset = result.get("id"), result.get("dataset")
    if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
        raise MetadataError("invalid_collection", "Collection id must be a lowercase slug")
    if not isinstance(dataset, str) or not DATASET.fullmatch(dataset):
        raise MetadataError("invalid_collection", "Collection requires a stable HF dataset identity")
    if result.get("recipe") not in ("simready", "scenario", "chemlab"):
        raise MetadataError("unsupported_recipe", "Supported recipes are simready, scenario and chemlab")
    if type(result.get("enabled", False)) is not bool:
        raise MetadataError("invalid_collection", "enabled must be a boolean")
    result.setdefault("enabled", False)
    result.setdefault("publisher", dataset.split("/")[0])
    result.setdefault("contentType", "3d-assets")
    result.setdefault("access", "unknown")
    result.setdefault("associated", [])
    result.setdefault("recipeVersion", RECIPE_VERSION)
    result.setdefault("enumerationUnit", "asset" if result["recipe"] != "scenario" else "example")
    if "revision" in result and result["revision"] is not None:
        revision(result["revision"])
    canonical(result)
    if len(canonical(result).encode()) > 64 * 1024:
        raise MetadataError("invalid_collection", "Collection profile exceeds 64 KiB")
    return result


def organization_fields(value):
    if not isinstance(value, dict) or set(value) - ORGANIZED_FIELDS:
        raise MetadataError("ungrounded_fields", "Organization can change descriptive fields only")
    result = deepcopy(value)
    for field in ("name", "summary"):
        if field in result:
            if not isinstance(result[field], dict) or set(result[field]) - {"en", "zh"}:
                raise MetadataError("invalid_metadata", f"{field} must contain en/zh text")
            result[field] = {
                lang: text(v, f"{field}.{lang}", empty=True) for lang, v in result[field].items()
            }
    for field in ("tags", "domains", "uses"):
        if field in result:
            values = result[field]
            if not isinstance(values, list) or len(values) > 64:
                raise MetadataError("invalid_metadata", f"{field} must have at most 64 values")
            result[field] = list(dict.fromkeys(text(v, field, 160) for v in values))
    if "category" in result and result["category"] is not None:
        result["category"] = text(result["category"], "category", 160)
    if "aliases" in result:
        aliases = result["aliases"]
        if not isinstance(aliases, dict) or set(aliases) - {"en", "zh"}:
            raise MetadataError("invalid_metadata", "aliases must contain en/zh lists")
        for lang, values in aliases.items():
            if not isinstance(values, list) or len(values) > 64:
                raise MetadataError("invalid_metadata", "aliases must have at most 64 values per language")
            aliases[lang] = list(dict.fromkeys(text(v, "alias", 160) for v in values))
    return result


def normalized_entry(collection_id, source_revision, value):
    if not isinstance(value, dict):
        raise MetadataError("invalid_metadata", "An inventory entry must be an object")
    result = deepcopy(value)
    upstream = text(result.get("upstreamId"), "upstreamId", 512)
    if any(ord(char) < 32 for char in upstream):
        raise MetadataError("invalid_metadata", "upstreamId contains control characters")
    result.update(collectionId=collection_id, upstreamId=upstream, revision=source_revision)
    result["id"] = f"{collection_id}/{quote(upstream, safe='._-')}@{source_revision}"
    result.setdefault("name", {"en": upstream.replace("_", " "), "zh": ""})
    result.setdefault("aliases", {"en": [], "zh": []})
    result.setdefault("summary", {"en": "", "zh": ""})
    result.setdefault("category", None)
    for field in ("tags", "domains", "uses"):
        result.setdefault(field, [])
    organization_fields({key: result[key] for key in ORGANIZED_FIELDS})
    result.setdefault("facts", {})
    if not isinstance(result["facts"], dict):
        raise MetadataError("invalid_metadata", "facts must be an object")
    result.setdefault("preview", {"state": "not_collected", "role": None, "path": None, "sha256": None})
    if not isinstance(result["preview"], dict):
        raise MetadataError("invalid_metadata", "Preview must be an object with an explicit state")
    if "thumbnail" in result["preview"]:
        raise MetadataError("ungrounded_fields", "Thumbnail derivatives are owned by the catalog host")
    result.setdefault("evidence", [])
    result.setdefault("fieldProvenance", {})
    result.setdefault("source", {})
    result.setdefault("vocabularyVersion", VOCABULARY_VERSION)
    result.setdefault("contentDigest", None)

    # Locator revisions alone do not change source contents. Every descriptive,
    # technical and preview input does, including content digests of evidence.
    def input_value(value):
        if isinstance(value, dict):
            return {key: input_value(item) for key, item in value.items() if key != "revision"}
        if isinstance(value, list):
            return [input_value(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"(?<=/)" + re.escape(source_revision) + r"(?=/|$)", "{revision}", value)
        return value

    source_input = {
        key: value
        for key, value in result.items()
        if key not in ("id", "revision", "inputDigest", "organization", "correctionVersion", "corrections")
    }
    computed_digest = digest(input_value(source_input))
    if result.get("inputDigest", computed_digest) != computed_digest:
        raise MetadataError("invalid_metadata", "inputDigest differs from its complete source input")
    result["inputDigest"] = computed_digest
    if len(canonical(result).encode()) > 512 * 1024:
        raise MetadataError("invalid_metadata", "An inventory entry exceeds 512 KiB")
    return result


def apply_fields(entry, fields):
    for key, value in fields.items():
        if key in ("name", "summary", "aliases"):
            entry[key] = {**entry.get(key, {}), **deepcopy(value)}
        else:
            entry[key] = deepcopy(value)
    return entry


def vocabulary_document(value):
    if not isinstance(value, dict):
        raise MetadataError("invalid_vocabulary", "Vocabulary must be an object")
    text(value.get("version"), "vocabulary.version", 160)
    for key, maximum in (("categories", 512), ("tags", 10000)):
        values = value.get(key, [])
        if not isinstance(values, list) or len(values) > maximum:
            raise MetadataError("invalid_vocabulary", f"Vocabulary {key} exceed their bounded list")
        identities = []
        for item in values:
            if not isinstance(item, dict):
                raise MetadataError("invalid_vocabulary", "Vocabulary terms must be objects")
            identities.append(text(item.get("id"), "vocabulary.id", 160))
            organization_fields({"name": item.get("name"), "aliases": item.get("aliases", {})})
        if len(identities) != len(set(identities)):
            raise MetadataError("invalid_vocabulary", "Vocabulary repeats a term identity")
    if "categories" not in value or len(canonical(value).encode()) > 2 * 1024 * 1024:
        raise MetadataError("invalid_vocabulary", "Vocabulary categories are required within 2 MiB")
    return deepcopy(value)


DEFAULT_PROFILES = (
    {
        "id": "simready",
        "dataset": "nvidia/simready-assets",
        "recipe": "simready",
        "enabled": False,
        "associated": [
            {
                "role": "viewer",
                "url": "https://huggingface.co/spaces/nvidia/simready-central",
                "revision": "e131715eb52d323bd903b1e51f81fb86bce96081",
                "path": "README.md",
            }
        ],
    },
    {
        "id": "scenario",
        "dataset": "scenario-labs/3d-collection",
        "recipe": "scenario",
        "enabled": False,
        "associated": [],
    },
    {
        "id": "chemlab",
        "dataset": "MBZUAI/ChemLab-Assets",
        "recipe": "chemlab",
        "enabled": False,
        "associated": [
            {
                "role": "documentation",
                "url": "https://github.com/mbzuai-nlp/ChemLab-Assets",
                "revision": "abee1cc5c38ed856979e13736417e3bfbd124d17",
                "path": "README_zh.md",
            }
        ],
    },
)


def default_vocabulary():
    categories = (
        ("container", "Container", "容器"),
        ("instrument", "Instrument", "仪器"),
        ("furniture", "Furniture", "家具"),
        ("tool", "Tool", "工具"),
        ("equipment", "Equipment", "设备"),
        ("labware", "Labware", "实验器具"),
        ("organic", "Organic object", "有机物"),
        ("vehicle", "Vehicle", "交通工具"),
        ("structure", "Structure", "建筑结构"),
        ("other", "Other", "其他"),
    )
    return {
        "version": VOCABULARY_VERSION,
        "categories": [
            {"id": key, "name": {"en": en, "zh": zh}, "aliases": {"en": [], "zh": []}}
            for key, en, zh in categories
        ],
        "tags": [],
    }
