"""Recording metadata compatibility, independent of the unchanged segment codec."""

import re

SCENE_VERSION = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*")
CONTENT_HASH = re.compile(r"[a-fA-F0-9]{64}")
PROPERTY_INDEX = re.compile(r"seg-[0-9]{6}\.index\.json")


def recording_metadata(value):
    """Return a read projection; never add derived fields to the file's original dict."""
    if not isinstance(value, dict) or type(value.get("formatVersion")) is not int:
        raise ValueError("Unsupported recording metadata")
    version = value["formatVersion"]
    if version == 1:
        # Historical crash metadata can lack a scene name; preserve it as unknown.
        scene = value.get("sceneId")
        return value | {
            "kind": "session", "sceneVersion": f"{scene}@1" if isinstance(scene, str) and scene else None,
            "sceneContentHash": None,
        }
    if version != 2 or value.get("protocolVersion") != 2:
        raise ValueError("Unsupported recording format/protocol version")
    if not {"kind", "sceneVersion", "sceneContentHash"}.issubset(value):
        raise ValueError("Recording v2 requires kind, sceneVersion and sceneContentHash")
    kind, scene, digest = value["kind"], value["sceneVersion"], value["sceneContentHash"]
    if kind == "session":
        if not isinstance(scene, str) or not SCENE_VERSION.fullmatch(scene):
            raise ValueError("Session recording v2 requires an exact scene version")
        if value.get("sceneId") != scene.partition("@")[0]:
            raise ValueError("Recording sceneId and sceneVersion disagree")
        if not isinstance(digest, str) or not CONTENT_HASH.fullmatch(digest):
            raise ValueError("Session recording v2 requires a scene content hash")
    elif kind == "live":
        if scene is not None or digest is not None:
            raise ValueError("Live recording scene version and content hash must be null")
    else:
        raise ValueError("Unknown recording kind")
    segments = value.get("segments", [])
    if not isinstance(segments, list) or any(not isinstance(segment, dict) for segment in segments):
        raise ValueError("Invalid recording segments")
    for segment in segments:
        reference = segment.get("propertyIndex")
        if reference is not None and (
            not isinstance(reference, str) or not PROPERTY_INDEX.fullmatch(reference)
        ):
            raise ValueError("Invalid recording property index reference")
    return dict(value)
