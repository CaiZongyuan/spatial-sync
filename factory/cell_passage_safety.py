"""Derive joint safety declarations from the retained physical USD assembly."""

import itertools
import math

from pxr import Usd, UsdGeom, UsdPhysics

from factory.cell_passage import LAB
from packages.protocol.math import rotate_vector


def swept_bounds(points, anchor, axis, limits):
    """Exact coordinate extrema of rotating collider-bound vertices."""
    if (not points or len(anchor) != 3 or len(axis) != 3 or len(limits) != 2
            or any(len(point) != 3 for point in points)
            or not all(math.isfinite(x) for row in [*points, anchor, axis, limits] for x in row)):
        raise ValueError("Finite collision vertices, hinge and limits are required")
    lower, upper = limits
    norm = math.sqrt(sum(x * x for x in axis))
    if lower > upper or abs(norm - 1) > 1e-6:
        raise ValueError("Ordered limits and a unit physical hinge axis are required")
    axis = [x / norm for x in axis]
    result = [[math.inf] * 3, [-math.inf] * 3]
    for point in points:
        vector = [point[i] - anchor[i] for i in range(3)]
        dot = sum(axis[i] * vector[i] for i in range(3))
        cosine = [vector[i] - axis[i] * dot for i in range(3)]
        sine = [axis[1] * vector[2] - axis[2] * vector[1],
                axis[2] * vector[0] - axis[0] * vector[2],
                axis[0] * vector[1] - axis[1] * vector[0]]
        center = [anchor[i] + axis[i] * dot for i in range(3)]
        for coordinate in range(3):
            a, b, c = cosine[coordinate], sine[coordinate], center[coordinate]
            if upper - lower >= 2 * math.pi:
                values = [c - math.hypot(a, b), c + math.hypot(a, b)]
            else:
                phase = math.atan2(b, a)
                angles = [lower, upper]
                angles.extend(phase + k * math.pi for k in range(
                    math.ceil((lower - phase) / math.pi), math.floor((upper - phase) / math.pi) + 1
                ))
                values = [c + a * math.cos(angle) + b * math.sin(angle) for angle in angles]
            result[0][coordinate] = min(result[0][coordinate], *values)
            result[1][coordinate] = max(result[1][coordinate], *values)
    return result


def rigid_target(stage, joint, relation):
    targets = relation.GetTargets()
    if len(targets) != 1 or not stage.GetPrimAtPath(targets[0]).HasAPI(UsdPhysics.RigidBodyAPI):
        raise ValueError("Joint must name one actual rigid body: " + str(joint.GetPath()))
    return stage.GetPrimAtPath(targets[0])


def joint_safety(stage, layout):
    robot = sorted(str(prim.GetPath()) for prim in Usd.PrimRange(stage.GetPrimAtPath(LAB + "/Robot"))
                   if prim.HasAPI(UsdPhysics.RigidBodyAPI))
    rail_joint = UsdPhysics.Joint(stage.GetPrimAtPath(LAB + "/Joints/RailCarriage"))
    carriage = str(rigid_target(stage, rail_joint, rail_joint.GetBody1Rel()).GetPath())
    bodies = [carriage, *robot]
    if len(robot) != 11 or len(set(bodies)) != 12:
        raise ValueError("Pinned rail/robot assembly must expose twelve distinct rigid bodies")
    common = {"armEntity": LAB + "/Robot/fr3_link0", "attachedLids": layout["lids"]}
    declarations = {LAB + "/RailCarriage": common | {"movingBodyKeys": bodies}}
    sources = {}
    cache, boxes = UsdGeom.XformCache(), UsdGeom.BBoxCache(0, ["default", "guide"])
    for name in ("OuterDoor", "InnerDoor"):
        joint = UsdPhysics.RevoluteJoint(stage.GetPrimAtPath(LAB + "/Joints/Incubator" + name))
        anchor = rigid_target(stage, joint, joint.GetBody0Rel())
        door = rigid_target(stage, joint, joint.GetBody1Rel())
        inverse = cache.GetLocalToWorldTransform(anchor).GetInverse()
        points, colliders = [], []
        for prim in Usd.PrimRange(door):
            if prim != door and prim.HasAPI(UsdPhysics.RigidBodyAPI):
                raise ValueError("Door sweep cannot ignore a nested moving rigid body")
            if not prim.HasAPI(UsdPhysics.CollisionAPI):
                continue
            bounds = boxes.ComputeUntransformedBound(prim).ComputeAlignedRange()
            low, high = bounds.GetMin(), bounds.GetMax()
            # Joint parent and door are siblings. Ancestor-relative USD APIs
            # cannot express this actual rigid-body frame.
            matrix = cache.GetLocalToWorldTransform(prim) * inverse
            points.extend(list(matrix.Transform(tuple(high[i] if corner[i] else low[i] for i in range(3))))
                          for corner in itertools.product((0, 1), repeat=3))
            colliders.append(str(prim.GetPath()))
        quaternion = joint.GetLocalRot0Attr().Get()
        direction = {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}[joint.GetAxisAttr().Get()]
        axis = rotate_vector([*quaternion.GetImaginary(), quaternion.GetReal()], direction)
        position = list(joint.GetLocalPos0Attr().Get())
        limits = [math.radians(joint.GetLowerLimitAttr().Get()),
                  math.radians(joint.GetUpperLimitAttr().Get())]
        sweep = swept_bounds(points, position, axis, limits)
        key = LAB + "/Incubator/" + name
        declarations[key] = common | {
            "lower": limits[0], "upper": limits[1],
            "movingBodyKeys": [str(door.GetPath())], "clearanceBodyKeys": bodies,
            "sweepAnchorBody": str(anchor.GetPath()),
            "sweepLowerLocalM": sweep[0], "sweepUpperLocalM": sweep[1],
        }
        sources[key] = {"joint": str(joint.GetPath()), "colliders": colliders,
                        "hingeLocalM": position, "axisLocal": axis, "physicalRangeRad": limits}
    return declarations, sources
