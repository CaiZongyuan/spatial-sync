"""Registered posture endpoints and observations; importing needs no SDK."""

import math
import re
import struct
from dataclasses import dataclass

from packages.library.physics_checks import check, tcp_error
from packages.programs.robot_arm.grasp import nominal_grasp_target, validate_height
from packages.protocol.math import normalize_quaternion


@dataclass(frozen=True)
class PostureEndpoint:
    field: str
    site: str
    height: float
    explicit: bool


@dataclass(frozen=True)
class PostureContext:
    site: str
    offset: tuple[float, float, float]
    joints: tuple[tuple[str, float], ...]


def resolve_contexts(fields, config, locations):
    """Opt-in target frames are separate from the physical endpoint contract."""
    marked = [name for name, role in fields.items() if "postureContext" in role]
    if not marked:
        return None
    if len(marked) != 1 or fields[marked[0]]["postureContext"] is not True:
        raise ValueError("Posture requires exactly one recognized postureContext role")
    values = config.get(marked[0])
    if (not isinstance(locations, dict) or not isinstance(values, dict)
            or set(values) != set(locations)):
        raise ValueError("Posture contexts must cover the taught locations exactly")
    result = {}
    for name, value in values.items():
        if not isinstance(value, dict) or set(value) != {"site", "offsetM", "joints"}:
            raise ValueError("Posture context requires only site, offsetM and joints")
        site, offset, joints = value["site"], value["offsetM"], value["joints"]
        if not isinstance(site, str) or not site.startswith("/"):
            raise ValueError("Posture context requires an actual Site path")
        if (not isinstance(offset, (list, tuple)) or len(offset) != 3
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in offset)):
            raise ValueError("Posture context offset must be a finite local vector")
        if not isinstance(joints, list):
            raise ValueError("Posture context joints must be an explicit list")
        positions = []
        for joint in joints:
            if not isinstance(joint, dict) or set(joint) != {"joint", "position"}:
                raise ValueError("Posture context joint requires joint and position")
            key, position = joint["joint"], joint["position"]
            if (not isinstance(key, str) or not key.startswith("/")
                    or type(position) not in (int, float) or not math.isfinite(position)):
                raise ValueError("Posture context joint requires an actual path and finite position")
            positions.append((key, float(position)))
        if len({key for key, _ in positions}) != len(positions):
            raise ValueError("Posture context contains duplicate joints")
        result[name] = PostureContext(site, tuple(map(float, offset)), tuple(positions))
    return result


def context_references(fields, config):
    if not any("postureContext" in role for role in fields.values()):
        return []
    documents = [name for name, role in fields.items() if role.get("documentSchema") == "locations"]
    if len(documents) != 1:
        raise ValueError("Posture context requires exactly one locations document")
    document = config.get(documents[0])
    if not isinstance(document, dict) or not isinstance(document.get("locations"), dict):
        raise ValueError("Posture context requires a resolved locations mapping")
    contexts = resolve_contexts(fields, config, document["locations"])
    result = []
    for context in contexts.values():
        result.append(("site", context.site))
        result.extend(("joint", key) for key, _ in context.joints)
    return result


def resolve_endpoint(fields, config, config_schema):
    """An explicit role wins; pre-height programs retain a unique zero Site."""
    marked = [name for name, role in fields.items() if "postureEndpoint" in role]
    if not marked:
        # Compatibility with the one introduced optional Config field: a legacy
        # program cannot silently ignore it when its new endpoint role is lost.
        if validate_height(config.get("graspHeightM", 0.0)) != 0:
            raise ValueError("A nonzero grasp height requires an explicit postureEndpoint")
        sites = [name for name, role in fields.items() if role.get("spatialReference") == "site"]
        if len(sites) != 1:
            raise ValueError("Legacy posture endpoint requires exactly one Site")
        name, height = sites[0], 0.0
    else:
        if len(marked) != 1:
            raise ValueError("Posture requires exactly one explicit endpoint")
        name = marked[0]
        information = fields[name]
        role = information["postureEndpoint"]
        if (
            information.get("spatialReference") != "site"
            or not isinstance(role, dict)
            or set(role) != {"offsetField", "offsetAxis"}
            or role["offsetAxis"] != "worldZ"
        ):
            raise ValueError("postureEndpoint must describe a Site and worldZ offset")
        field = role["offsetField"]
        if not isinstance(field, str):
            raise ValueError("postureEndpoint offsetField must name a declared Config scalar")
        declaration = config_schema.get("properties", {}).get(field)
        if (
            not isinstance(declaration, dict)
            or declaration.get("type") != "number"
            or type(declaration.get("minimum")) not in (int, float)
            or not math.isfinite(declaration["minimum"])
            or declaration["minimum"] < 0
        ):
            raise ValueError("postureEndpoint offsetField requires a declared nonnegative number")
        height = validate_height(config.get(field, declaration.get("default")))
    site = config.get(name)
    if not isinstance(site, str) or not site:
        raise ValueError("Posture endpoint Config must name one actual Site")
    return PostureEndpoint(name, site, height, bool(marked))


def require_calibration(endpoint, expected, observed, *, pose_storage=None):
    """Compare trusted pinned-asset extraction with composed/model observations.

    Neither a metadata role nor a boolean receipt supplies physical calibration.
    Extractors remain responsible for deriving these records from verified bytes.
    """
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        raise ValueError("Physical endpoint calibration is unavailable")
    for key in ("assetId", "assetContentHash", "handBody", "tcpSite", "siteOwner", "geometryDigest"):
        value = expected.get(key)
        if not isinstance(value, str) or not value or observed.get(key) != value:
            raise ValueError("Physical endpoint calibration differs: " + key)
    for key in ("assetContentHash", "geometryDigest"):
        if not re.fullmatch(r"[0-9a-f]{64}", expected[key]):
            raise ValueError("Physical endpoint calibration requires verified digests")
    if (
        "@" not in expected["assetId"]
        or expected["siteOwner"] != expected["handBody"]
        or expected["tcpSite"] != endpoint.site
    ):
        raise ValueError("Physical endpoint calibration owner or Site differs")
    first, second = expected.get("handToTcpPose"), observed.get("handToTcpPose")
    if (
        not isinstance(first, (list, tuple))
        or not isinstance(second, (list, tuple))
        or len(first) != 7
        or len(second) != 7
    ):
        raise ValueError("Physical endpoint calibration requires actual local poses")
    if not all(type(value) in (int, float) and math.isfinite(value) for value in [*first, *second]):
        raise ValueError("Physical endpoint calibration local pose must be finite")
    if pose_storage == "float32":
        # The importer stores the directly extracted USD local pose in
        # wp.transform (float32). Compare exact representable values, not a
        # spatial-distance allowance that could hide a moved TCP.
        first = [struct.unpack("f", struct.pack("f", value))[0] for value in first]
    elif pose_storage is not None:
        raise ValueError("Physical endpoint calibration pose storage is unknown")
    if list(first[:3]) != list(second[:3]):
        raise ValueError("Physical endpoint calibration local position differs")
    a, b = normalize_quaternion(first[3:]), normalize_quaternion(second[3:])
    if a != b and a != tuple(-value for value in b):
        raise ValueError("Physical endpoint calibration local rotation differs")


def posture_check(
    entity,
    location,
    physical_tcp,
    bottom_site,
    endpoint,
    *,
    expected_calibration=None,
    observed_calibration=None,
    pose_storage=None,
):
    """Never report a point-distance pass before a nonzero physical-frame check."""
    try:
        if endpoint.height:
            require_calibration(endpoint, expected_calibration, observed_calibration,
                                pose_storage=pose_storage)
        target = nominal_grasp_target(bottom_site, endpoint.height)
    except (ValueError, TypeError) as error:
        return check("P6", "fail", str(error), entity=entity, location=location)
    row = tcp_error(entity, location, list(physical_tcp), list(target))
    if endpoint.explicit:
        measurements = row.setdefault("measurements", {})
        measurements.update(
            siteWorldM=list(bottom_site),
            bottomSiteWorld=list(bottom_site),
            graspHeightM=endpoint.height,
            graspTargetWorld=list(target),
            physicalTcpWorld=list(physical_tcp),
            targetDefinition="tube-grasp-height/1",
        )
    return row
