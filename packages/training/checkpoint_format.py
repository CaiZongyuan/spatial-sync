"""Bounded, pickle-free subset of the safetensors wire format and its PPO metadata.

Also bundled into the isolated trainer. This module has no trainer dependencies.
"""

import hashlib
import io
import json
import math
import re
import struct
import tarfile

MAX_BYTES = 128 * 1024 * 1024
MAX_METADATA = 1024 * 1024
MAX_TENSORS = 4096
DTYPE_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "I16": 2,
    "I32": 4,
    "I64": 8,
    "F16": 2,
    "BF16": 2,
    "F32": 4,
    "F64": 8,
}
FILES = ("checkpoint.json", "checkpoint.safetensors")
STATE_KEYS = {"actor_state_dict", "critic_state_dict", "optimizer_state_dict"}
RND_KEYS = {"rnd_state_dict", "rnd_optimizer_state_dict"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()


def decode_json(raw):
    def object_pairs(pairs):
        value = dict(pairs)
        if len(pairs) != len(value):
            raise ValueError("Duplicate checkpoint JSON key")
        return value

    def nonfinite(_):
        raise ValueError("Non-finite checkpoint JSON value")

    if len(raw) > MAX_METADATA:
        raise ValueError("Checkpoint JSON exceeds its limit")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs, parse_constant=nonfinite)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid checkpoint JSON") from exc


def safeguard_tensors(raw):
    if not 8 < len(raw) <= MAX_BYTES:
        raise ValueError("Invalid safetensors size")
    (size,) = struct.unpack_from("<Q", raw)
    if not 0 < size <= MAX_METADATA or 8 + size > len(raw) or raw[8] != ord("{"):
        raise ValueError("Invalid safetensors header size")
    header = decode_json(raw[8 : 8 + size])
    if not isinstance(header, dict) or not 1 <= len(header) <= MAX_TENSORS:
        raise ValueError("Invalid safetensors tensor table")
    buffers = memoryview(raw)[8 + size :]
    spans = []
    for name, spec in header.items():
        if (
            not re.fullmatch(r"t[0-9]{1,4}", name)
            or not isinstance(spec, dict)
            or set(spec) != {"dtype", "shape", "data_offsets"}
        ):
            raise ValueError("Invalid safetensors entry")
        dtype, shape, offsets = spec["dtype"], spec["shape"], spec["data_offsets"]
        if (
            not isinstance(dtype, str)
            or dtype not in DTYPE_BYTES
            or not isinstance(shape, list)
            or len(shape) > 16
            or any(type(dim) is not int or not 0 <= dim <= MAX_BYTES for dim in shape)
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or any(type(bound) is not int for bound in offsets)
        ):
            raise ValueError("Invalid safetensors shape, dtype or offset")
        start, end = offsets
        if not 0 <= start <= end <= len(buffers) or end - start != math.prod(shape) * DTYPE_BYTES[dtype]:
            raise ValueError("Invalid safetensors byte range")
        spans.append((start, end))
    cursor = 0
    for start, end in sorted(spans):
        if start != cursor:
            raise ValueError("Overlapping or sparse safetensors data")
        cursor = end
    if cursor != len(buffers):
        raise ValueError("Unexpected trailing safetensors data")
    return header, buffers


def _check_state(value, references, depth=0):
    if depth > 20:
        raise ValueError("Checkpoint state is nested too deeply")
    if isinstance(value, dict):
        if set(value) == {"tensor"}:
            name = value["tensor"]
            if not isinstance(name, str) or name in references:
                raise ValueError("Invalid or duplicate tensor reference")
            references.add(name)
        else:
            for key, child in value.items():
                if not key or len(key) > 512:
                    raise ValueError("Invalid checkpoint state key")
                _check_state(child, references, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_state(child, references, depth + 1)
    elif type(value) is float and not math.isfinite(value):
        raise ValueError("Non-finite checkpoint state")
    elif value is not None and type(value) not in (int, float, bool, str):
        raise ValueError("Invalid checkpoint state")


def read_checkpoint(raw):
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("Checkpoint exceeds its size limit")
    values = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for entry in archive:
                if entry.name not in FILES or entry.name in values or not entry.isfile() or entry.sparse:
                    raise ValueError("Checkpoint archive contains an unexpected entry")
                limit = MAX_METADATA if entry.name == "checkpoint.json" else MAX_BYTES
                if not 0 < entry.size <= limit:
                    raise ValueError("Invalid checkpoint entry size")
                with archive.extractfile(entry) as stream:
                    values[entry.name] = stream.read()
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("Invalid checkpoint archive") from exc
    if set(values) != set(FILES):
        raise ValueError("Checkpoint requires both JSON and safetensors")
    metadata = decode_json(values[FILES[0]])
    tensors = values[FILES[1]]
    if (
        not isinstance(metadata, dict)
        or set(metadata)
        != {
            "schemaVersion",
            "jobId",
            "leaseId",
            "inputsSha256",
            "runtime",
            "iteration",
            "environmentSteps",
            "learningRate",
            "tensorSha256",
            "state",
        }
        or type(metadata["schemaVersion"]) is not int
        or metadata["schemaVersion"] != 1
        or any(
            type(metadata[key]) is not int or not 0 <= metadata[key] <= 2**53 - 1
            for key in ("iteration", "environmentSteps")
        )
        or metadata["iteration"] == 0
        or type(metadata["learningRate"]) not in (int, float)
        or not 0 < metadata["learningRate"] < math.inf
        or metadata["tensorSha256"] != digest(tensors)
    ):
        raise ValueError("Invalid checkpoint metadata or safetensors digest")
    state = metadata["state"]
    if not isinstance(state, dict) or set(state) not in (STATE_KEYS, STATE_KEYS | RND_KEYS):
        raise ValueError("Invalid checkpoint PPO state sections")
    references = set()
    _check_state(state, references)
    header, buffers = safeguard_tensors(tensors)
    if references != header.keys():
        raise ValueError("Checkpoint tensor references differ from the safetensors table")
    for key in ("actor_state_dict", "critic_state_dict"):
        if (
            not isinstance(state[key], dict)
            or not state[key]
            or any(not isinstance(value, dict) or set(value) != {"tensor"} for value in state[key].values())
        ):
            raise ValueError("Checkpoint requires tensor-only actor and critic state")
    return metadata, tensors, header, buffers


def pack_checkpoint(metadata, tensors):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in zip(FILES, (json_bytes(metadata), tensors), strict=True):
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    raw = output.getvalue()
    if len(raw) > MAX_BYTES:
        raise ValueError("Checkpoint exceeds its size limit")
    return raw
