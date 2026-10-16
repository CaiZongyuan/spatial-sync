"""Resolve logical selections and conservative physics-world viewport membership."""

from dataclasses import dataclass
from math import hypot, isfinite, sqrt

from packages.protocol.schema import SceneManifest

Plane = tuple[float, float, float, float]


@dataclass(frozen=True)
class ScopeFilter:
    key: tuple
    stream_ids: frozenset[int] | None
    planes: tuple[Plane, ...] = ()
    radii: dict[int, float | None] | None = None

    def select(self, poses):
        if self.planes:
            result = []
            for pose in poses:
                radius = self.radii.get(pose[0])
                if radius is None or all(
                    nx * pose[1] + ny * pose[2] + nz * pose[3] + d >= -radius for nx, ny, nz, d in self.planes
                ):
                    result.append(pose)
            return result
        if self.stream_ids is None:
            return poses
        return [pose for pose in poses if pose[0] in self.stream_ids]


def visual_radius(visual) -> float | None:
    if visual.kind != "primitive":
        return None  # An unknown mesh/GLB must remain visible even when its origin is outside.
    geometry = visual.geometry
    if geometry.type == "box":
        radius = hypot(*geometry.halfExtents)
    elif geometry.type == "sphere":
        radius = geometry.radius
    elif geometry.type == "capsule":
        radius = geometry.radius + geometry.halfHeight
    elif geometry.type in ("cylinder", "cone"):
        radius = hypot(geometry.radius, geometry.halfHeight)
    else:
        if geometry.width == 0 or geometry.length == 0:
            return None
        radius = hypot(geometry.width / 2, geometry.length / 2)
    return hypot(*visual.localPose.p) + radius


class ScopeResolver:
    def __init__(self, manifest: SceneManifest):
        self.nodes = {node.entityKey: node for node in manifest.nodes}
        self.sites = {site.entityKey: site for site in manifest.sites}
        self.joints = {joint.entityKey: joint for joint in manifest.joints}
        self.entities = self.nodes.keys() | self.sites.keys() | self.joints.keys()
        self.body_ids = {node.entityKey: node.streamId for node in manifest.nodes if node.kind == "body"}
        self.children = {key: [] for key in self.nodes}
        for node in manifest.nodes:
            if node.parentEntityKey is not None:
                self.children[node.parentEntityKey].append(node.entityKey)
        self.radii = {}
        for node in manifest.nodes:
            if node.kind != "body":
                continue
            visuals = [*node.visuals, *(node.fallbackVisuals or []), *node.colliders]
            radii = [visual_radius(visual) for visual in visuals]
            self.radii[node.streamId] = None if None in radii else max(radii, default=sqrt(3) * 0.1)

    def resolve(self, scope: str, *, entity_keys=(), device_key=None, viewport_planes=()) -> ScopeFilter:
        if scope == "all":
            return ScopeFilter(("all",), None)
        if scope == "viewport":
            if len(viewport_planes) != 6:
                raise ValueError("viewport requires six physics-world clipping planes")
            planes = []
            for plane in viewport_planes:
                if len(plane) != 4 or not all(isfinite(value) for value in plane):
                    raise ValueError("viewport planes must contain four finite values")
                magnitude = hypot(*plane[:3])
                if (
                    not isfinite(magnitude)
                    or magnitude < 1e-12
                    or not all(isfinite(x / magnitude) for x in plane)
                ):
                    raise ValueError("viewport plane normal cannot be zero")
                planes.append(tuple(value / magnitude for value in plane))
            normalized = tuple(planes)
            return ScopeFilter(("viewport", normalized), None, normalized, self.radii)
        if scope == "device":
            node = self.nodes.get(device_key)
            if node is None or node.kind != "group":
                raise ValueError("deviceKey must identify a Manifest group")
            requested = {device_key}
        elif scope == "entities":
            requested = set(entity_keys)
            if not requested or not requested <= self.entities:
                raise ValueError("entities scope requires known entityKeys")
        else:
            raise ValueError("unknown subscription scope")
        included = set(requested)
        pending = list(requested & self.nodes.keys())
        while pending:
            key = pending.pop()
            for child in self.children[key]:
                if child not in included:
                    included.add(child)
                    pending.append(child)
        for key in requested:
            site = self.sites.get(key)
            if site and site.parentBodyKey:
                included.add(site.parentBodyKey)
            joint = self.joints.get(key)
            if joint:
                included.add(joint.childBodyKey)
                if joint.parentBodyKey:
                    included.add(joint.parentBodyKey)
        ids = frozenset(stream_id for key, stream_id in self.body_ids.items() if key in included)
        return ScopeFilter((scope, tuple(sorted(requested))), ids)
