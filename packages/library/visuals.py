"""Inspect GLB geometry and texture budgets from bounded package bytes."""

import base64
import json
import struct
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote


def image_dimensions(value):
    if value.startswith(b"\x89PNG\r\n\x1a\n") and len(value) >= 24:
        return struct.unpack(">II", value[16:24])
    if value.startswith(b"\xff\xd8"):
        cursor = 2
        while cursor + 4 <= len(value):
            if value[cursor] != 255:
                cursor += 1
                continue
            marker = value[cursor + 1]
            cursor += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(value[cursor : cursor + 2], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3) and length >= 7:
                return (
                    int.from_bytes(value[cursor + 5 : cursor + 7], "big"),
                    int.from_bytes(value[cursor + 3 : cursor + 5], "big"),
                )
            if length < 2:
                break
            cursor += length
    if len(value) >= 30 and value[:4] == b"RIFF" and value[8:16] == b"WEBPVP8X":
        return (1 + int.from_bytes(value[24:27], "little"), 1 + int.from_bytes(value[27:30], "little"))
    raise ValueError("Unsupported or malformed embedded texture")


def glb_budget(path, package=None):
    try:
        return inspect_glb(path, package)
    except (KeyError, IndexError, TypeError, AttributeError, struct.error) as exc:
        raise ValueError("Malformed GLB document, buffer, or accessor") from exc


def inspect_glb(path, package=None):
    value = Path(path).read_bytes()
    if len(value) < 20 or value[:4] != b"glTF" or struct.unpack_from("<I", value, 4)[0] != 2:
        raise ValueError("Visual is not a glTF2 binary")
    if struct.unpack_from("<I", value, 8)[0] != len(value):
        raise ValueError("GLB length does not match its bytes")
    chunks, cursor = {}, 12
    while cursor + 8 <= len(value):
        length, kind = struct.unpack_from("<II", value, cursor)
        cursor += 8
        if length % 4 or cursor + length > len(value) or kind in chunks:
            raise ValueError("GLB chunk exceeds its bytes")
        chunks[kind] = value[cursor : cursor + length]
        cursor += length
    if cursor != len(value):
        raise ValueError("GLB has trailing or incomplete chunks")
    document = json.loads(chunks[0x4E4F534A])
    if document["asset"]["version"] != "2.0":
        raise ValueError("Visual is not a glTF2 document")

    def indexed(values, index):
        if type(index) is not int or not 0 <= index < len(values):
            raise ValueError("GLB index does not name an existing item")
        return values[index]

    def resource(uri):
        if uri.startswith("data:"):
            header, separator, encoded = uri.partition(",")
            if not separator or not header.endswith(";base64"):
                raise ValueError("GLB inline resources require base64 data URIs")
            return base64.b64decode(encoded, validate=True)
        uri = unquote(uri)
        if (
            package is None
            or PureWindowsPath(uri).drive
            or ":" in uri
            or "\\" in uri
            or PurePosixPath(uri).is_absolute()
        ):
            raise ValueError("GLB resource must resolve to an indexed package-local file")
        target = (Path(path).parent / uri).resolve()
        root = Path(package["path"]).resolve()
        if not target.is_relative_to(root) or target.relative_to(root).as_posix() not in package["files"]:
            raise ValueError("GLB resource escapes its package or is not indexed")
        return target.read_bytes()

    buffers = document.get("buffers", [])
    binaries = []
    for index, buffer in enumerate(buffers):
        binary = resource(buffer["uri"]) if "uri" in buffer else chunks[0x004E4942] if index == 0 else b""
        if type(buffer["byteLength"]) is not int or not 0 <= buffer["byteLength"] <= len(binary):
            raise ValueError("GLB buffer exceeds its bounded package bytes")
        binaries.append(binary)
    views = document.get("bufferViews", [])
    for view in views:
        start, length = view.get("byteOffset", 0), view["byteLength"]
        if (
            type(view["buffer"]) is not int
            or view["buffer"] < 0
            or type(start) is not int
            or type(length) is not int
            or start < 0
            or length < 0
            or start + length > indexed(buffers, view["buffer"])["byteLength"]
        ):
            raise ValueError("GLB bufferView exceeds its embedded buffer")

    def accessor_count(index, *, positions=False):
        accessor = indexed(document["accessors"], index)
        count, offset = accessor["count"], accessor.get("byteOffset", 0)
        components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
        widths = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
        width = widths[accessor["componentType"]] * components[accessor["type"]]
        if type(count) is not int or count <= 0 or type(offset) is not int or offset < 0:
            raise ValueError("GLB accessor has invalid count or offset")
        if positions and (accessor["type"] != "VEC3" or accessor["componentType"] != 5126):
            raise ValueError("GLB POSITION must use floating point VEC3 coordinates")
        if "bufferView" in accessor:
            view = indexed(views, accessor["bufferView"])
            stride = view.get("byteStride", width)
            if (
                type(stride) is not int
                or stride < width
                or offset + (count - 1) * stride + width > view["byteLength"]
            ):
                raise ValueError("GLB accessor exceeds its bufferView")
        elif "sparse" not in accessor:
            raise ValueError("GLB accessor has neither embedded nor sparse data")
        if "sparse" in accessor:
            sparse = accessor["sparse"]
            if type(sparse["count"]) is not int or not 0 < sparse["count"] <= count:
                raise ValueError("GLB sparse accessor has invalid count")
            for field, item_width in (
                ("indices", widths[sparse["indices"]["componentType"]]),
                ("values", width),
            ):
                item = sparse[field]
                if item.get("byteOffset", 0) < 0 or (
                    item.get("byteOffset", 0) + sparse["count"] * item_width
                    > indexed(views, item["bufferView"])["byteLength"]
                ):
                    raise ValueError("GLB sparse accessor exceeds its bufferView")
        return count

    triangles = 0
    for mesh in document.get("meshes", []):
        for primitive in mesh["primitives"]:
            count = accessor_count(primitive["attributes"]["POSITION"], positions=True)
            if "indices" in primitive:
                count = accessor_count(primitive["indices"])
                accessor = indexed(document["accessors"], primitive["indices"])
                if accessor["type"] != "SCALAR" or accessor["componentType"] not in (5121, 5123, 5125):
                    raise ValueError("GLB indices must be unsigned scalar coordinates")
            mode = primitive.get("mode", 4)
            if type(mode) is not int or mode not in range(7) or (mode == 4 and count % 3):
                raise ValueError("GLB primitive mode or triangle coordinate count is invalid")
            triangles += count // 3 if mode == 4 else max(0, count - 2) if mode in (5, 6) else 0
    if triangles > 500000:
        raise ValueError(f"Visual has {triangles} triangles; maximum is 500000")
    for image in document.get("images", []):
        if "bufferView" in image:
            view = indexed(views, image["bufferView"])
            start = view.get("byteOffset", 0)
            pixels = binaries[view["buffer"]][start : start + view["byteLength"]]
        else:
            pixels = resource(image["uri"])
        width, height = image_dimensions(pixels)
        if min(width, height) <= 0 or max(width, height) > 4096:
            raise ValueError(f"Texture is {width}x{height}; maximum is 4096x4096")
    return triangles
