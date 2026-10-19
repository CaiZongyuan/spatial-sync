"""Budget boundaries use real GLB binary buffers and encoded PNG texture bytes."""

import json
import struct
import zlib

import pytest
import trimesh

from packages.library.structural import glb_budget


def glb(path, triangles=1, *, texture=None, change=None):
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    indices = struct.pack("<III", 0, 1, 2) * triangles
    binary = positions + indices
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "type": "VEC3", "count": 3},
            {"bufferView": 1, "componentType": 5125, "type": "SCALAR", "count": triangles * 3},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
    }
    if texture:
        document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(texture)})
        document["images"] = [{"bufferView": 2, "mimeType": "image/png"}]
        binary += texture
        document["buffers"][0]["byteLength"] = len(binary)
    if change:
        change(document)
    metadata = json.dumps(document).encode()
    metadata += b" " * (-len(metadata) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = (
        struct.pack("<II", len(metadata), 0x4E4F534A)
        + metadata
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
    return path


def png(width, height):
    def chunk(kind, contents):
        return (
            struct.pack(">I", len(contents))
            + kind
            + contents
            + struct.pack(">I", zlib.crc32(kind + contents))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\0\0\0" * width) * height))
        + chunk(b"IEND", b"")
    )


def test_real_exported_box_has_twelve_triangles(tmp_path):
    path = tmp_path / "box.glb"
    path.write_bytes(trimesh.creation.box().export(file_type="glb"))
    assert glb_budget(path) == 12


def test_triangle_budget_boundary_and_actual_oversized_index_buffer(tmp_path):
    assert glb_budget(glb(tmp_path / "boundary.glb", 500000)) == 500000
    with pytest.raises(ValueError, match="500001 triangles"):
        glb_budget(glb(tmp_path / "oversized.glb", 500001))


def test_encoded_texture_budget_boundary_and_oversized_texture(tmp_path):
    assert glb_budget(glb(tmp_path / "boundary.glb", texture=png(4096, 1))) == 1
    with pytest.raises(ValueError, match="4097x1"):
        glb_budget(glb(tmp_path / "oversized.glb", texture=png(4097, 1)))


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc["accessors"][1].update(count=300),
        lambda doc: doc["bufferViews"][0].update(byteLength=1000),
        lambda doc: doc["accessors"][0].update(bufferView=500),
        lambda doc: doc["meshes"][0]["primitives"][0].update(indices="not-an-index"),
    ],
)
def test_malformed_visual_buffers_and_accessors_fail_with_a_validation_error(tmp_path, change):
    with pytest.raises(ValueError):
        glb_budget(glb(tmp_path / "invalid.glb", change=change))
