"""Property measurements from existing LWLG bytes and optional verified sidecars."""

import json
import math
import re
from dataclasses import dataclass
from hashlib import sha256

from packages.info.log import RECORD_HEADER, _atomic_json, _json_bytes, read_segment
from packages.info.store import StateStore
from packages.protocol.schema import EntityObservations, EntitySnapshot

LIMIT = 2000
DURATION = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h|d)")
UNITS = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}


@dataclass(frozen=True)
class Sample:
    key: str
    property: str
    t_ms: float
    value: object
    revision: str
    epoch: int
    offset: int
    snapshot: bool = False
    entity_index: int = 0


def duration(value):
    match = DURATION.fullmatch(value)
    if match is None or not math.isfinite(result := float(match[1]) * UNITS[match[2]]) or result <= 0:
        raise ValueError("Expected a positive duration with ms, s, m, h or d suffix")
    return result


def query_window(since, until, every, end):
    """Numeric bounds are raw sampleTimeMs; durations look back from the chosen end."""
    upper = end if until is None else float(until)
    lower = upper - duration(since) if DURATION.fullmatch(since) else float(since)
    interval = None if every is None else duration(every)
    if not all(math.isfinite(value) for value in (lower, upper, upper - lower)) or lower > upper:
        raise ValueError("History requires finite since <= until in recording sampleTimeMs")
    if interval is not None and not math.isfinite((upper - lower) / interval):
        raise ValueError("History interval is too small for the requested range")
    return lower, upper, interval


def scan_samples(raw):
    """Use the live reducer's acceptance rules before exposing actual measurements."""
    store = None
    for record in read_segment(raw).records:
        if record.kind != 2:
            continue
        message = json.loads(record.payload)
        if not isinstance(message, dict):
            raise ValueError("Invalid reliable recording message")
        kind = message.get("type")
        if kind not in ("entity.snapshot", "entity.observations"):
            continue
        if store is None:
            store = StateStore(message["sceneRevision"])
        model = (EntitySnapshot if kind == "entity.snapshot" else EntityObservations).model_validate(message)
        entities = message["entities"] if kind == "entity.snapshot" else message["changes"]
        if kind == "entity.snapshot" and not store.apply(model):
            continue
        for entity_index, entity in enumerate(entities):
            old = store.entities.get(entity["key"]) if kind == "entity.observations" else None
            if kind == "entity.observations":
                # A batch can contain the same key more than once. Advance in
                # FIFO order, exactly as the live reducer does for that batch.
                update = model.model_copy(update={"changes": [model.changes[entity_index]]})
                if not store.apply(update):
                    continue
            if kind == "entity.observations" and old and entity["rev"] < old.rev:
                continue
            for prop, value in entity["values"].items():
                meta = entity.get("meta", {}).get(prop)
                # Missing snapshot metadata has no known measurement time. A
                # roll's envelope time must never turn it into a new sample.
                if meta is None and kind == "entity.snapshot":
                    continue
                meta = meta or message
                at = meta["sampleTimeMs"]
                if kind == "entity.observations" and old and prop in old.meta:
                    if at < old.meta[prop].sampleTimeMs:
                        continue
                    if (at == old.meta[prop].sampleTimeMs and value == old.values.get(prop)
                            and meta.get("stale", False) != old.meta[prop].stale):
                        continue  # A freshness transition is not a new measurement.
                yield Sample(entity["key"], prop, at, value, message["sceneRevision"],
                             message["epoch"], record.offset, kind == "entity.snapshot", entity_index)


def write_property_index(path):
    """Called only by the disk owner after close; the sidecar is disposable."""
    raw = path.read_bytes()
    properties = {}
    for sample in scan_samples(raw):
        entry = properties.setdefault((sample.key, sample.property), {
            "key": sample.key, "property": sample.property,
            "startSampleTimeMs": sample.t_ms, "endSampleTimeMs": sample.t_ms, "records": [],
        })
        entry["startSampleTimeMs"] = min(entry["startSampleTimeMs"], sample.t_ms)
        entry["endSampleTimeMs"] = max(entry["endSampleTimeMs"], sample.t_ms)
        # JSON arrays stay identical; immutable numeric tuples can be untracked by GC.
        entry["records"].append((sample.t_ms, sample.offset, sample.epoch, sample.entity_index))
    document = {"version": 1, "segmentSha256": sha256(raw).hexdigest(), "bytes": len(raw),
                "properties": list(properties.values())}
    document["checksum"] = sha256(_json_bytes(document)).hexdigest()
    target = path.with_suffix(".index.json")
    _atomic_json(target, document)
    return target.name


def indexed_samples(raw, path, key, prop, since, until):
    """Reject corrupt/stale sidecars as a whole, then use their real LWLG offsets."""
    document = json.loads(path.read_bytes())
    if not isinstance(document, dict):
        raise ValueError("Invalid property index")
    checksum = document.pop("checksum")
    if (checksum != sha256(_json_bytes(document)).hexdigest() or document["version"] != 1
            or document["bytes"] != len(raw) or document["segmentSha256"] != sha256(raw).hexdigest()):
        raise ValueError("Stale or corrupt property index")
    entries = document["properties"]
    if not isinstance(entries, list):
        raise ValueError("Invalid property index")
    samples = []
    for entry in entries:
        if entry["key"] != key or entry["property"] != prop:
            continue
        if entry["endSampleTimeMs"] < since or entry["startSampleTimeMs"] > until:
            continue
        for at, offset, epoch, entity_index in entry["records"]:
            if not since <= at <= until:
                continue
            if type(offset) is not int or not 6 <= offset <= len(raw) - RECORD_HEADER.size:
                raise ValueError("Invalid property record offset")
            length, kind, _ = RECORD_HEADER.unpack_from(raw, offset)
            if kind != 2 or length < 9 or offset + 4 + length > len(raw):
                raise ValueError("Invalid property record")
            message = json.loads(raw[offset + RECORD_HEADER.size:offset + 4 + length])
            entities = message["entities"] if message["type"] == "entity.snapshot" else message["changes"]
            if type(entity_index) is not int or not 0 <= entity_index < len(entities):
                raise ValueError("Invalid property entity offset")
            entity = entities[entity_index]
            meta = entity.get("meta", {}).get(prop) or message
            if (entity["key"] != key or at != meta["sampleTimeMs"]
                    or epoch != message["epoch"] or not math.isfinite(at)):
                raise ValueError("Property index disagrees with its record")
            samples.append(Sample(key, prop, at, entity["values"][prop], message["sceneRevision"],
                                  epoch, offset, message["type"] == "entity.snapshot", entity_index))
    return samples


def read_samples(directory, metadata, key, prop, since, until):
    samples = []
    for segment in metadata["segments"]:
        name = segment["file"]
        if not re.fullmatch(r"seg-[0-9]{6}\.bin", name):
            raise ValueError("Invalid recording segment name")
        path = directory / name
        if path.is_symlink():
            raise ValueError("Recording segments cannot be symbolic links")
        if type(segment["bytes"]) is not int or segment["bytes"] < 6:
            raise ValueError("Invalid recording segment byte count")
        # A live checkpoint owns this valid byte prefix, even as the writer continues.
        with path.open("rb") as source:
            raw = source.read(segment["bytes"])
        reference = segment.get("propertyIndex")
        try:
            if not reference or reference != path.with_suffix(".index.json").name:
                raise ValueError("No property index")
            index = directory / reference
            if index.is_symlink():
                raise ValueError("Property indexes cannot be symbolic links")
            selected = indexed_samples(raw, index, key, prop, since, until)
        except (OSError, ValueError, KeyError, TypeError):
            selected = [sample for sample in scan_samples(raw)
                        if sample.key == key and sample.property == prop and since <= sample.t_ms <= until]
        samples.extend(selected)
    # Stable sorting retains recorded order at identical times. Roll snapshots
    # repeat a measurement; context identity keeps genuine reset samples distinct.
    seen, result = set(), []
    for sample in sorted(samples, key=lambda sample: sample.t_ms):
        identity = (sample.revision, sample.epoch, sample.t_ms, _json_bytes(sample.value))
        if sample.snapshot and identity in seen:
            continue
        seen.add(identity)
        result.append(sample)
    return result


def aggregate(samples, since, until, every, agg):
    selected = [sample for sample in samples if since <= sample.t_ms <= until]
    if every is None:
        points = [[sample.t_ms, sample.value] for sample in selected]
    else:
        buckets = {}
        for sample in selected:
            bucket = (sample.revision, sample.epoch, math.floor((sample.t_ms - since) / every))
            buckets.setdefault(bucket, []).append(sample)
        points = []
        for values in buckets.values():
            last = values[-1]
            if agg == "last":
                value = last.value
            else:
                if any(type(sample.value) not in (int, float) for sample in values):
                    raise ValueError("mean, min and max require numeric measurements; use agg=last")
                numbers = [sample.value for sample in values]
                try:
                    value = (math.fsum(number / len(numbers) for number in numbers) if agg == "mean"
                             else min(numbers) if agg == "min" else max(numbers))
                except OverflowError as error:
                    raise ValueError("History aggregate exceeds the finite numeric range") from error
            points.append([last.t_ms, value])
        points.sort(key=lambda point: point[0])
    return points[:LIMIT], len(points) > LIMIT
