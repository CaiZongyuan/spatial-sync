"""Validated, immutable data structures for the committed external asset catalog.

The gateway loads this module from repository data; it never imports ``factory``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from packages.library.common import KINDS

CATALOG_SCHEMA = "lw.asset-catalog/1"
SYNCED_CATALOG_SCHEMA = "lw.asset-catalog/2"
COLLECTION_SCHEMA = "lw.asset-catalog/3"
SEARCH_SCHEMA = "lw.catalog-search/1"
ENTRY_SCHEMA = "lw.catalog-entry/1"
ESTIMATE_SCHEMA = "lw.catalog-estimate/1"
SNAPSHOT_PREFIX = "sha256:"

REVISION = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
LOCAL_ASSET_ID = re.compile(
    r"(?:robot|device|labware|environment)/[a-z0-9]+(?:-[a-z0-9]+)*"
)
USAGE_NAMES = ("dressing", "staticCollision")
ADAPTATION_STATUSES = ("pass", "gap")
LIMIT_KEYS = (
    "sourcePackageBytes",
    "adaptedPackageBytes",
    "batchBytes",
    "rawCacheBytes",
    "freeDiskMinBytes",
)
# Platform defaults from the SF0 support boundary, stored here so the runtime
# never imports factory/. A catalog file may override them explicitly.
DEFAULT_LIMITS = {
    "sourcePackageBytes": 536870912,
    "adaptedPackageBytes": 536870912,
    "batchBytes": 2147483648,
    "rawCacheBytes": 10737418240,
    "freeDiskMinBytes": 10737418240,
    "concurrentAcquisitions": 2,
}


class CatalogFormatError(ValueError):
    """The committed catalog file does not match the public schema."""


def _mapping(value, name):
    if not isinstance(value, dict):
        raise CatalogFormatError(f"{name} must be an object")
    return value


def _list(value, name, *, maximum=256):
    if not isinstance(value, list) or len(value) > maximum:
        raise CatalogFormatError(f"{name} must be a list of at most {maximum} items")
    return value


def _string(value, name, *, maximum=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CatalogFormatError(f"{name} must be a non-empty string of at most {maximum} characters")
    return value


def _boolean(value, name):
    if not isinstance(value, bool):
        raise CatalogFormatError(f"{name} must be a boolean")
    return value


def _integer(value, name, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CatalogFormatError(f"{name} must be an integer >= {minimum}")
    return value


def _positive_number(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not value > 0
    ):
        raise CatalogFormatError(f"{name} must be a positive finite number")
    return float(value)


def _string_list(value, name, *, maximum=128, item_maximum=500):
    return tuple(
        _string(item, f"{name}[]", maximum=item_maximum)
        for item in _list(value, name, maximum=maximum)
    )


def _bilingual(value, name, *, maximum=1_000_000):
    pairs = _mapping(value, name)
    for language in ("en", "zh"):
        if language not in pairs:
            raise CatalogFormatError(f"{name}.{language} is required")
    return {language: _string(pairs[language], f"{name}.{language}", maximum=maximum)
            for language in ("en", "zh")}


def _bilingual_lists(value, name, *, item_maximum=100_000, maximum=128):
    pairs = _mapping(value, name)
    return {language: _string_list(pairs.get(language, []), f"{name}.{language}",
                                  maximum=maximum, item_maximum=item_maximum)
            for language in ("en", "zh")}


@dataclass(frozen=True)
class Source:
    url: str
    files: int | None
    bytes: int | None
    root_usd: str | None
    bom_items: int | None
    bom_not_in_bom: tuple[str, ...]

    def document(self):
        return {
            "url": self.url,
            "files": self.files,
            "bytes": self.bytes,
            "rootUsd": self.root_usd,
            "bomItems": self.bom_items,
            "bomNotInBom": list(self.bom_not_in_bom),
        }


@dataclass(frozen=True)
class LicenseEvidence:
    file: str | None
    sha256: str | None


@dataclass(frozen=True)
class LicenseAttachment:
    file: str
    license: str
    evidence: str


@dataclass(frozen=True)
class License:
    name: str | None
    admitted: bool
    obligations: tuple[str, ...]
    in_bom: bool | None
    evidence: LicenseEvidence
    attachments: tuple[LicenseAttachment, ...]
    unknown: bool = False

    def document(self):
        return {
            "name": self.name,
            "admitted": self.admitted,
            "obligations": list(self.obligations),
            "inBom": self.in_bom,
            "evidence": {"file": self.evidence.file, "sha256": self.evidence.sha256},
            "attachments": [
                {"file": item.file, "license": item.license, "evidence": item.evidence}
                for item in self.attachments
            ],
        }


@dataclass(frozen=True)
class UsageFact:
    eligible: bool | None
    unknown: bool
    evidence: str

    def document(self):
        return {"eligible": self.eligible, "unknown": self.unknown, "evidence": self.evidence}


@dataclass(frozen=True)
class Adaptation:
    status: str
    gaps: tuple[str, ...]
    dimensions_m: tuple[float, ...] | None
    output_bytes: int | None
    output_files: int | None
    variant: str
    approximations: tuple[str, ...]

    def document(self):
        return {
            "status": self.status,
            "gaps": list(self.gaps),
            "dimensionsM": list(self.dimensions_m) if self.dimensions_m is not None else None,
            "outputBytes": self.output_bytes,
            "outputFiles": self.output_files,
            "variant": self.variant,
            "approximations": list(self.approximations),
        }


@dataclass(frozen=True)
class UpstreamValidation:
    profile: str
    profile_version: str
    date: str
    failing_features: tuple[str, ...]
    declared_by: str

    def document(self, *, declared=True):
        value = {
            "profile": self.profile,
            "profileVersion": self.profile_version,
            "date": self.date,
            "failingFeatures": list(self.failing_features),
        }
        if declared:
            value["declaredBy"] = self.declared_by
        return value


@dataclass(frozen=True)
class Acquisition:
    eligible: bool
    reasons: tuple[str, ...]

    def document(self):
        return {"eligible": self.eligible, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class CatalogEntry:
    provider: str
    revision: str
    package_id: str
    local_asset_id: str | None
    kind: str
    source: Source
    names: dict
    aliases: dict
    descriptions: dict
    tags: dict
    license: License
    usage: dict
    adaptation: Adaptation
    upstream_validation: UpstreamValidation
    acquisition: Acquisition
    preview: dict | None = None
    discovery: dict | None = None

    @property
    def id(self):
        return f"{self.provider}/{self.package_id}@{self.revision}"

    @property
    def ref(self):
        return f"catalog:{self.id}"

    def usage_value(self, name):
        fact = self.usage[name]
        return fact.eligible

    def unknown_attributes(self):
        unknown = []
        if self.kind == "unknown":
            unknown.append("kind")
        if self.source.bytes is None:
            unknown.append("sourceBytes")
        if self.source.files is None:
            unknown.append("sourceFiles")
        if self.license.unknown:
            unknown.append("license")
        if self.adaptation.dimensions_m is None:
            unknown.append("dimensionsM")
        for name in USAGE_NAMES:
            if self.usage[name].eligible is None:
                unknown.append(name)
        return unknown


@dataclass(frozen=True)
class CatalogSnapshot:
    id: str
    provider: str
    dataset: str
    revision: str
    indexed_at: datetime
    indexed_at_text: str
    coverage: str
    entry_count: int
    limits: dict
    sync: dict | None = None
    sources: tuple = ()

    def document(self, now=None):
        now = now or datetime.now(UTC)
        age = max(0, int((now - self.indexed_at).total_seconds()))
        result = {
            "id": self.id,
            "provider": self.provider,
            "providerRevision": self.revision,
            "indexedAt": self.indexed_at_text,
            "entryCount": self.entry_count,
            "coverage": self.coverage,
            "offlineReadable": True,
            "networkRequired": False,
            "ageSeconds": age,
        }
        if self.sync is not None:
            result["sync"] = self.sync
        if self.sources:
            result["sources"] = list(self.sources)
        return result


def unavailable_snapshot_document():
    return {
        "id": None,
        "provider": None,
        "providerRevision": None,
        "indexedAt": None,
        "entryCount": 0,
        "coverage": None,
        "offlineReadable": False,
        "networkRequired": False,
        "ageSeconds": None,
    }


def _reject_constant(token):
    raise CatalogFormatError(f"Non-finite JSON number is not allowed: {token}")


def _source(value):
    data = _mapping(value, "source")
    return Source(
        url=_string(data.get("url"), "source.url", maximum=10_000),
        files=_integer(data.get("files"), "source.files"),
        bytes=_integer(data.get("bytes"), "source.bytes"),
        root_usd=_string(data.get("rootUsd"), "source.rootUsd", maximum=2000),
        bom_items=_integer(data.get("bomItems"), "source.bomItems"),
        bom_not_in_bom=_string_list(data.get("bomNotInBom", []), "source.bomNotInBom",
                                    maximum=512, item_maximum=10_000),
    )


def _license(value):
    data = _mapping(value, "license")
    evidence = _mapping(data.get("evidence"), "license.evidence")
    digest = _string(evidence.get("sha256"), "license.evidence.sha256", maximum=64)
    if not SHA256.fullmatch(digest):
        raise CatalogFormatError("license.evidence.sha256 must be a SHA-256 hex digest")
    attachments = []
    for item in _list(data.get("attachments", []), "license.attachments", maximum=64):
        entry = _mapping(item, "license.attachments[]")
        attachments.append(LicenseAttachment(
            file=_string(entry.get("file"), "license.attachments[].file", maximum=2000),
            license=_string(entry.get("license"), "license.attachments[].license", maximum=1000),
            evidence=_string(entry.get("evidence"), "license.attachments[].evidence", maximum=1000),
        ))
    return License(
        name=_string(data.get("name"), "license.name", maximum=1000),
        admitted=_boolean(data.get("admitted"), "license.admitted"),
        obligations=_string_list(data.get("obligations", []), "license.obligations",
                                 maximum=128, item_maximum=10_000),
        in_bom=_boolean(data.get("inBom"), "license.inBom"),
        evidence=LicenseEvidence(
            file=_string(evidence.get("file"), "license.evidence.file", maximum=2000),
            sha256=digest,
        ),
        attachments=tuple(attachments),
    )


def _usage_fact(value, name):
    data = _mapping(value, name)
    eligible = data.get("eligible")
    if eligible is not None:
        eligible = _boolean(eligible, f"{name}.eligible")
    unknown = _boolean(data.get("unknown"), f"{name}.unknown")
    if unknown != (eligible is None):
        raise CatalogFormatError(f"{name}: unknown must be true exactly when eligible is null")
    return UsageFact(eligible, unknown, _string(data.get("evidence"), f"{name}.evidence",
                                                maximum=100_000))


def _usage(value):
    data = _mapping(value, "usage")
    return {name: _usage_fact(data.get(name), f"usage.{name}") for name in USAGE_NAMES}


def _adaptation(value):
    data = _mapping(value, "adaptation")
    status = _string(data.get("status"), "adaptation.status", maximum=20)
    if status not in ADAPTATION_STATUSES:
        raise CatalogFormatError("adaptation.status must be pass or gap")
    dimensions = data.get("dimensionsM")
    if dimensions is not None:
        dimensions = tuple(
            _positive_number(item, "adaptation.dimensionsM[]")
            for item in _list(dimensions, "adaptation.dimensionsM", maximum=3)
        )
        if len(dimensions) != 3:
            raise CatalogFormatError("adaptation.dimensionsM must contain three values")
    output_bytes = data.get("outputBytes")
    output_files = data.get("outputFiles")
    if output_bytes is not None:
        output_bytes = _integer(output_bytes, "adaptation.outputBytes", minimum=0)
    if output_files is not None:
        output_files = _integer(output_files, "adaptation.outputFiles", minimum=0)
    gaps = _string_list(data.get("gaps", []), "adaptation.gaps", maximum=128,
                        item_maximum=100_000)
    if status == "pass" and (dimensions is None or output_bytes is None or output_files is None):
        raise CatalogFormatError("a passing adaptation requires dimensionsM, outputBytes and outputFiles")
    if status == "gap" and not gaps:
        raise CatalogFormatError("a gap adaptation requires at least one gap")
    return Adaptation(
        status,
        gaps,
        dimensions,
        output_bytes,
        output_files,
        _string(data.get("variant"), "adaptation.variant", maximum=1000),
        _string_list(data.get("approximations", []), "adaptation.approximations",
                     maximum=128, item_maximum=100_000),
    )


def _upstream(value):
    data = _mapping(value, "upstreamValidation")
    declared_by = data.get("declaredBy", "not recorded")
    return UpstreamValidation(
        profile=_string(data.get("profile"), "upstreamValidation.profile", maximum=1000),
        profile_version=_string(data.get("profileVersion"), "upstreamValidation.profileVersion",
                                maximum=200),
        date=_string(data.get("date"), "upstreamValidation.date", maximum=200),
        failing_features=_string_list(data.get("failingFeatures", []),
                                      "upstreamValidation.failingFeatures",
                                      maximum=512, item_maximum=10_000),
        declared_by=_string(declared_by, "upstreamValidation.declaredBy", maximum=1000),
    )


def _acquisition(value):
    data = _mapping(value, "acquisition")
    eligible = _boolean(data.get("eligible"), "acquisition.eligible")
    reasons = _string_list(data.get("reasons", []), "acquisition.reasons",
                           maximum=128, item_maximum=100_000)
    if not eligible and not reasons:
        raise CatalogFormatError("a non-eligible acquisition requires at least one reason")
    return Acquisition(eligible, reasons)


def _entry(value, provider, revision):
    data = _mapping(value, "entries[]")
    package_id = _string(data.get("packageId"), "entries[].packageId", maximum=200)
    if not PACKAGE.fullmatch(package_id):
        raise CatalogFormatError(f"Invalid package ID: {package_id}")
    local_asset_id = _string(data.get("localAssetId"), "entries[].localAssetId", maximum=200)
    if not LOCAL_ASSET_ID.fullmatch(local_asset_id):
        raise CatalogFormatError(f"Invalid local asset ID: {local_asset_id}")
    kind = _string(data.get("kind"), "entries[].kind", maximum=50)
    if kind not in KINDS:
        raise CatalogFormatError(f"Unknown entry kind: {kind}")
    return CatalogEntry(
        provider=provider,
        revision=revision,
        package_id=package_id,
        local_asset_id=local_asset_id,
        kind=kind,
        source=_source(data.get("source")),
        names=_bilingual(data.get("names"), "names", maximum=100_000),
        aliases=_bilingual_lists(data.get("aliases"), "aliases"),
        descriptions=_bilingual(data.get("descriptions"), "descriptions", maximum=1_000_000),
        tags=_bilingual_lists(data.get("tags"), "tags", item_maximum=10_000),
        license=_license(data.get("license")),
        usage=_usage(data.get("usage")),
        adaptation=_adaptation(data.get("adaptation")),
        upstream_validation=_upstream(data.get("upstreamValidation")),
        acquisition=_acquisition(data.get("acquisition")),
    )


def _limits(value):
    limits = dict(DEFAULT_LIMITS)
    if value is None:
        return limits
    data = _mapping(value, "limits")
    for key in LIMIT_KEYS:
        if key in data:
            limits[key] = _integer(data[key], f"limits.{key}", minimum=1)
    if "concurrentAcquisitions" in data:
        limits["concurrentAcquisitions"] = _integer(
            data["concurrentAcquisitions"], "limits.concurrentAcquisitions", minimum=1
        )
    return limits


def _discovered_entry(value, provider, revision):
    """Unreviewed source facts cannot manufacture an acquisition admission.

    Version 1 remains strict. Version 2 additionally represents discovery-only
    packages with genuinely unknown sizes, classifications and license evidence.
    The trusted seed overlay is embedded by the synchronizer only at its exact
    reviewed revision; it is never taken from an upstream metadata document.
    """
    data = _mapping(value, "entries[]")
    package = _string(data.get("packageId"), "packageId", maximum=200)
    if not PACKAGE.fullmatch(package) or package in (".", ".."):
        raise CatalogFormatError("Invalid discovered package ID")
    source = _mapping(data.get("source"), "source")

    def optional_integer(name):
        value = source.get(name)
        return None if value is None else _integer(value, f"source.{name}")

    preview = _mapping(data.get("preview"), "preview")
    url = preview.get("url")
    expected = f"/api/v1/library/catalog/entries/{provider}/{package}/{revision}/preview"
    if url is not None and url != expected and not re.fullmatch(
        r"/api/v1/library/catalog/previews/[0-9a-f]{64}\.(?:png|jpg|webp)", url
    ):
        raise CatalogFormatError("A cached preview must be a local content-addressed image")
    preview = {
        "kind": "on-demand" if url == expected else "cached" if url else "unavailable", "url": url,
        "reason": _string(preview.get("reason"), "preview.reason", maximum=2000),
    }
    discovery = _mapping(data.get("discovery"), "discovery")
    if "curated" in data:
        curated = _entry(data["curated"], provider, revision)
        if curated.package_id != package:
            raise CatalogFormatError("Curated overlay must match the discovered package")
        return replace(curated, preview=preview, discovery=discovery)
    name = _string(data.get("title"), "title", maximum=2000)
    license_name = data.get("declaredLicense")
    if license_name is not None:
        license_name = _string(license_name, "declaredLicense", maximum=1000)
    missing = "Not reviewed for adaptation or acquisition; source declarations are not platform checks."
    return CatalogEntry(
        provider, revision, package, None, "unknown",
        Source(_string(source.get("url"), "source.url", maximum=10000),
               optional_integer("files"), optional_integer("bytes"), source.get("rootUsd"),
               optional_integer("bomItems"), ()),
        {"en": name, "zh": name}, {"en": [package], "zh": []},
        {"en": "Name derived from the upstream package ID; " + missing,
         "zh": "名称由上游包 ID 整理；尚未审查许可、适配或物理用途。"},
        {"en": [], "zh": []},
        License(license_name, False, (), None, LicenseEvidence(None, None), (), unknown=True),
        {name: UsageFact(None, True, missing) for name in USAGE_NAMES},
        Adaptation("gap", (missing,), None, None, None, "not reviewed", ()),
        UpstreamValidation("not reviewed", "unknown", "unknown", (), "not verified"),
        Acquisition(False, (missing,)), preview, discovery,
    )


def load_catalog(path):
    """Read and validate the exact bytes that define the snapshot identity."""
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogFormatError(f"Catalog is not valid UTF-8 JSON: {exc}") from exc
    return parse_catalog(data, raw)


def parse_catalog(data, raw=None):
    raw = raw if raw is not None else json.dumps(data, sort_keys=True, allow_nan=False).encode()
    data = _mapping(data, "catalog")
    if data.get("schema") == COLLECTION_SCHEMA:
        groups = _list(data.get("sources"), "sources", maximum=100)
        snapshots, entries = [], []
        for group in groups:
            group = _mapping(group, "sources[]")
            if group.get("schema") not in (CATALOG_SCHEMA, SYNCED_CATALOG_SCHEMA):
                raise CatalogFormatError("A collection source must be a v1 or v2 catalog")
            snapshot, values = parse_catalog(group)
            snapshots.append(snapshot)
            entries.extend(values)
        if not snapshots or len({s.provider for s in snapshots}) != len(snapshots):
            raise CatalogFormatError("Collection providers must be present and unique")
        if len(entries) > 100000:
            raise CatalogFormatError("Collection exceeds 100000 entries")
        latest = max(snapshots, key=lambda s: s.indexed_at)
        single = snapshots[0] if len(snapshots) == 1 else None
        return CatalogSnapshot(
            id=SNAPSHOT_PREFIX + hashlib.sha256(raw).hexdigest(),
            provider=single.provider if single else "multiple",
            dataset=single.dataset if single else "multiple",
            revision=single.revision if single else hashlib.sha1(raw).hexdigest(),
            indexed_at=latest.indexed_at, indexed_at_text=latest.indexed_at_text,
            coverage="Complete lightweight directories; details and previews load on demand.",
            entry_count=len(entries), limits=_limits(data.get("limits")),
            sync={key: sum((s.sync or {}).get(key, 0) for s in snapshots) for key in (
                "discovered", "indexed", "metadataFailed", "previewsCached", "previewsReused",
                "previewsMissing", "previewsFailed",
            )},
            sources=tuple({"provider": s.provider, "dataset": s.dataset,
                           "revision": s.revision, "entryCount": s.entry_count,
                           "indexedAt": s.indexed_at_text} for s in snapshots),
        ), tuple(entries)
    synced = data.get("schema") == SYNCED_CATALOG_SCHEMA
    if data.get("schema") not in (CATALOG_SCHEMA, SYNCED_CATALOG_SCHEMA):
        raise CatalogFormatError(f"catalog.schema must be {CATALOG_SCHEMA} or {SYNCED_CATALOG_SCHEMA}")
    provider = _string(data.get("provider"), "provider", maximum=100)
    dataset = _string(data.get("dataset"), "dataset", maximum=300)
    revision = _string(data.get("revision"), "revision", maximum=40)
    if not REVISION.fullmatch(revision):
        raise CatalogFormatError("revision must be a full 40-character lowercase Git revision")
    indexed_at = _string(data.get("indexedAt"), "indexedAt", maximum=64)
    try:
        indexed = datetime.fromisoformat(indexed_at)
    except ValueError as exc:
        raise CatalogFormatError("indexedAt must be an ISO-8601 timestamp") from exc
    if indexed.tzinfo is None:
        raise CatalogFormatError("indexedAt must include a timezone")
    coverage = _string(data.get("coverage"), "coverage", maximum=10_000)
    entries = []
    for value in _list(data.get("entries"), "entries", maximum=100000 if synced else 10000):
        entry = (_discovered_entry if synced else _entry)(value, provider, revision)
        if synced:
            entry = replace(entry, discovery=entry.discovery | {"dataset": dataset})
        entries.append(entry)
    package_ids = [entry.package_id for entry in entries]
    if len(package_ids) != len(set(package_ids)):
        raise CatalogFormatError("Entry package IDs must be unique")
    snapshot = CatalogSnapshot(
        id=SNAPSHOT_PREFIX + hashlib.sha256(raw).hexdigest(),
        provider=provider,
        dataset=dataset,
        revision=revision,
        indexed_at=indexed.astimezone(UTC),
        indexed_at_text=indexed_at,
        coverage=coverage,
        entry_count=len(entries),
        limits=_limits(data.get("limits")),
        sync=_mapping(data.get("sync"), "sync") if synced else None,
    )
    return snapshot, tuple(entries)
