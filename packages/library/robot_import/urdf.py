"""Read the bounded rigid URDF subset without ROS package or network resolvers."""

import numpy as np
from trimesh import transformations as tf

from packages.library.archive import invalid
from packages.library.robot_import.model import Body, Joint, Robot, Shape, mesh_resource, vector
from packages.library.robot_import.source import MESH_SUFFIXES, TEXTURE_SUFFIXES


def origin(node):
    location = node.find("origin")
    if location is None:
        return np.eye(4)
    matrix = tf.euler_matrix(*vector(location.get("rpy", "0 0 0"), 3))
    matrix[:3, 3] = vector(location.get("xyz", "0 0 0"), 3)
    return matrix


def inertial(body, item):
    if (mass := item.find("mass")) is not None and float(mass.attrib["value"]) > 0:
        body.mass = float(mass.attrib["value"])
        body.center = origin(item)[:3, 3]
    if (tensor := item.find("inertia")) is not None:
        xx, xy, xz, yy, yz, zz = (float(tensor.attrib.get(key, 0))
                                  for key in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz"))
        rotation = origin(item)[:3, :3]
        tensor = rotation @ np.array([[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]]) @ rotation.T
        if np.any(tensor):
            body.inertia = tensor


def color(source, entry, material, materials):
    if material is None:
        return (.65, .65, .65, 1.)
    definition = materials.get(material.get("name"), material) if not len(material) else material
    if (texture := definition.find("texture")) is not None:
        target = source.resolve(entry, texture.attrib["filename"], TEXTURE_SUFFIXES)
        source.texture_bytes(target)
        raise invalid("URDF texture mapping requires a supported textured visual format", path=target)
    rgba = definition.find("color")
    return tuple(vector(rgba.attrib["rgba"], 4)) if rgba is not None else (.65, .65, .65, 1.)


def shape(source, entry, item, materials):
    geometry = item.find("geometry")
    if geometry is None or len(geometry) != 1:
        raise ValueError("Require exactly one geometry per visual or collider")
    element = geometry[0]
    size, mesh = (), None
    if element.tag == "box":
        size = tuple(vector(element.attrib["size"], 3))
    elif element.tag == "sphere":
        size = (float(element.attrib["radius"]),)
    elif element.tag in ("cylinder", "capsule"):
        size = (float(element.attrib["radius"]), float(element.attrib["length"]))
    elif element.tag == "mesh":
        target = source.resolve(entry, element.attrib["filename"], MESH_SUFFIXES)
        mesh = mesh_resource(source, target)
        mesh.apply_scale(vector(element.get("scale", "1 1 1"), 3))
    else:
        raise ValueError(f"Unsupported URDF geometry {element.tag}")
    return Shape(element.tag, size, origin(item), item.tag == "collision", item.tag == "visual",
                 color(source, entry, item.find("material"), materials), mesh=mesh)


def load(source, entry):
    document = source.xml(entry)
    if document.tag != "robot" or any(node.tag.endswith("include") for node in document.iter()):
        raise invalid("Expected expanded URDF without include or xacro", path=entry)
    if any(node.tag in ("gazebo", "plugin") for node in document.iter()):
        raise invalid("Unsupported URDF simulation extension", path=entry)
    for node in document.iter("mesh"):
        source.resolve(entry, node.get("filename", ""), MESH_SUFFIXES)
    for node in document.iter("texture"):
        source.resolve(entry, node.get("filename", ""), TEXTURE_SUFFIXES)
    materials = {node.get("name"): node for node in document.findall("material")}
    bodies, joints = {}, []
    for node in document.findall("link"):
        name = node.get("name", "")
        if not name or name in bodies:
            raise invalid("Link names must be nonempty and unique", path=name or entry)
        try:
            body = bodies[name] = Body(name)
            if (item := node.find("inertial")) is not None:
                inertial(body, item)
            body.shapes = [shape(source, entry, item, materials) for item in node
                           if item.tag in ("collision", "visual")]
        except (ValueError, KeyError) as exc:
            raise invalid(str(exc), path=name) from exc

    known = set()
    for node in document.findall("joint"):
        name, kind = node.get("name", ""), node.get("type")
        if not name or name in known:
            raise invalid("Joint names must be nonempty and unique", path=name or entry)
        known.add(name)
        kinds = {"fixed": "fixed", "revolute": "hinge", "continuous": "hinge", "prismatic": "slide"}
        if kind not in kinds or node.find("mimic") is not None:
            raise invalid(f"Unsupported URDF joint or coupling ({kind})", path=name)
        try:
            parent, child = (node.find(key).attrib["link"] for key in ("parent", "child"))
            if child not in bodies or parent not in bodies:
                raise ValueError("Joint endpoint does not name a link")
            bodies[child].pose = origin(node)
            joint = Joint(name, kinds[kind], parent, child)
            if (axis := node.find("axis")) is not None:
                joint.axis = vector(axis.attrib["xyz"], 3)
            if (limit := node.find("limit")) is not None:
                lower, upper = limit.get("lower"), limit.get("upper")
                if (lower is None) != (upper is None):
                    raise ValueError("Joint limits require both endpoints")
                if lower is not None and kind not in ("fixed", "continuous"):
                    joint.limits = float(lower), float(upper)
                    if not joint.limits[0] < joint.limits[1]:
                        raise ValueError("Joint lower limit must be below its upper limit")
                if (effort := float(limit.get("effort", "0"))) > 0:
                    joint.force = effort
            if (dynamics := node.find("dynamics")) is not None:
                joint.kv = float(dynamics.get("damping", "0"))
                joint.frictionloss = float(dynamics.get("friction", "0"))
        except (ValueError, KeyError, AttributeError) as exc:
            raise invalid(str(exc), path=name) from exc
        joints.append(joint)
    return Robot(bodies, joints, fixed=False,
                 exclusions={tuple(sorted((joint.parent, joint.child))) for joint in joints})
