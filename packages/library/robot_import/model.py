"""Supported rigid robot geometry and source-coordinate articulation topology."""

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath

import numpy as np
import trimesh
from trimesh import transformations as tf

from packages.library.archive import invalid


@dataclass
class Shape:
    kind: str
    size: tuple
    pose: np.ndarray
    collision: bool
    visual: bool
    color: tuple = (.65, .65, .65, 1.)
    friction: float = 1.
    mesh: trimesh.Trimesh | None = None
    convex: bool = False
    restitution: float = 0.

    def surface(self):
        match self.kind:
            case "box":
                return trimesh.creation.box(extents=self.size)
            case "sphere":
                return trimesh.creation.icosphere(subdivisions=2, radius=self.size[0])
            case "cylinder":
                return trimesh.creation.cylinder(radius=self.size[0], height=self.size[1], sections=32)
            case "capsule":
                return trimesh.creation.capsule(radius=self.size[0], height=self.size[1], count=(16, 24))
            case "mesh":
                return self.mesh.copy()
        raise ValueError(f"Unsupported geometry {self.kind}")


@dataclass
class Body:
    name: str
    pose: np.ndarray = field(default_factory=lambda: np.eye(4))
    mass: float | None = None
    center: np.ndarray = field(default_factory=lambda: np.zeros(3))
    inertia: np.ndarray | None = None
    shapes: list[Shape] = field(default_factory=list)
    sites: dict = field(default_factory=dict)


@dataclass
class Joint:
    name: str
    kind: str
    parent: str
    child: str
    axis: np.ndarray = field(default_factory=lambda: np.array([1., 0., 0.]))
    anchor: np.ndarray = field(default_factory=lambda: np.zeros(3))
    limits: tuple[float, float] | None = None
    reference: float = 0.
    initial: float = 0.
    kp: float = 0.
    kv: float = 0.
    armature: float = 0.
    force: float | None = None
    frictionloss: float = 0.


@dataclass
class Robot:
    bodies: dict[str, Body]
    joints: list[Joint]
    fixed: bool
    exclusions: set = field(default_factory=set)
    up: str = "Z"

    def ordered(self):
        incoming = {}
        children = {name: [] for name in self.bodies}
        for joint in self.joints:
            if joint.parent not in children or joint.child not in children:
                raise invalid("Joint endpoint does not name a robot link", path=joint.name)
            if joint.child in incoming:
                raise invalid("Closed chain or multiply parented link", path=joint.name)
            incoming[joint.child] = joint
            children[joint.parent].append(joint.child)
        roots = set(children) - incoming.keys()
        if len(roots) != 1:
            raise invalid("Expected one connected robot root", path="joints")
        queue = [roots.pop()]
        ordered = []
        while queue:
            name = queue.pop()
            ordered.append((self.bodies[name], incoming.get(name)))
            queue.extend(sorted(children[name], key=lambda child: incoming[child].name, reverse=True))
        if len(ordered) != len(children):
            raise invalid("Closed or disconnected robot articulation", path="joints")
        if len(ordered) > 128 or not 0 < sum(j.kind != "fixed" for j in self.joints) <= 64:
            raise invalid("Robot requires 1–64 scalar coordinates and at most 128 links", path="joints")
        return ordered


def vector(value, count):
    result = np.array([float(item) for item in value.split()], dtype=float)
    if result.shape != (count,) or not np.isfinite(result).all():
        raise ValueError(f"Expected {count} finite numbers")
    return result


def transform(pos, quat):
    pose = tf.quaternion_matrix(quat)
    pose[:3, 3] = pos
    return pose


def mesh_resource(source, name):
    suffix = PurePosixPath(name).suffix.lower()
    try:
        mesh = trimesh.load(BytesIO(source.mesh_bytes(name)), file_type=suffix[1:],
                            resolver=None, force="mesh", process=False)
    except (ValueError, TypeError, IndexError) as exc:
        raise invalid("Unreadable robot mesh", path=name) from exc
    if (not isinstance(mesh, trimesh.Trimesh) or not 0 < len(mesh.faces) <= 500000
            or not np.isfinite(mesh.vertices).all()):
        raise invalid("Empty, oversized or non-finite robot mesh", path=name)
    return mesh
