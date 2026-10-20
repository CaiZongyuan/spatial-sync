"""Export immutable browser geometry once, retaining Newton local/world semantics."""

from importlib.metadata import version

import newton
import warp as wp

from packages.assets.bindings import apply_bindings
from packages.protocol.math import normalize_quaternion
from packages.protocol.schema import SceneManifest

from .labels import labels_for_model
from .mesh_assets import MeshStore


def _pose(transform):
    return {"p": [float(x) for x in transform[:3]], "q": list(normalize_quaternion(transform[3:]))}


def _primitive(shape_type, scale):
    x, y, z = (float(v) for v in scale)
    if shape_type == newton.GeoType.BOX:
        return {"type": "box", "halfExtents": [x, y, z]}
    if shape_type == newton.GeoType.SPHERE:
        return {"type": "sphere", "radius": x}
    if shape_type in (newton.GeoType.CAPSULE, newton.GeoType.CYLINDER, newton.GeoType.CONE):
        if shape_type == newton.GeoType.CYLINDER and z != 0:
            return None
        return {"type": newton.GeoType(shape_type).name.lower(), "radius": x, "halfHeight": y}
    if shape_type == newton.GeoType.PLANE:
        return {"type": "plane", "width": 2 * x, "length": 2 * y}
    return None


def export_manifest(
    sim,
    *,
    session_id: str,
    scene_revision: str,
    revision_numeric_id: int,
    publish_hz=30.0,
    realtime_factor=1.0,
    strict_labels=False,
) -> SceneManifest:
    model = sim.model
    sim.meshes = MeshStore()
    builder = getattr(sim, "builder", None)
    site_aliases = getattr(builder, "spatial_site_visuals", {})
    labels = labels_for_model(
        model,
        missing_labels=getattr(sim, "missing_labels", ()),
        strict=strict_labels,
        site_aliases=site_aliases,
    )
    warnings = list(labels.warnings)
    reserved = {item.entity_key for item in [*labels.shapes, *labels.joints]
                if item.kind in ("site", "joint")}

    def node_parent(key):
        parent = key.rsplit("/", 1)[0]
        while parent in reserved:
            parent = parent.rsplit("/", 1)[0]
        return parent
    shape_bodies = model.shape_body.numpy()
    shape_flags = model.shape_flags.numpy()
    transforms = model.shape_transform.numpy()
    shape_types = model.shape_type.numpy()
    scales = model.shape_scale.numpy()
    nodes = []
    body_nodes = {}
    static_nodes = {}
    sites = []
    anchor_to_joint = {shape: joint for joint, shape in sim.anchor_sites.items()}
    for identity in labels.bodies:
        node = {
            "entityKey": identity.entity_key,
            "kind": "body",
            "streamId": identity.index,
            "name": identity.entity_key.rsplit("/", 1)[-1],
            "stableKey": identity.stable,
            "visuals": [],
            "colliders": [],
            "debug": {"newtonBodyIndex": identity.index},
        }
        nodes.append(node)
        body_nodes[identity.index] = node

    for shape, identity in enumerate(labels.shapes):
        body = int(shape_bodies[shape])
        if shape_flags[shape] & int(newton.ShapeFlags.SITE):
            joint = anchor_to_joint.get(shape)
            sites.append(
                {
                    "entityKey": identity.entity_key,
                    "role": "joint-anchor" if joint is not None else "generic",
                    "parentBodyKey": labels.bodies[body].entity_key if body >= 0 else None,
                    "jointKey": labels.joints[joint].entity_key if joint is not None else None,
                    "localPose": _pose(transforms[shape]),
                    "stableKey": identity.stable,
                }
            )
            continue
        geometry = _primitive(shape_types[shape], scales[shape])
        visual = None
        if shape_types[shape] in (newton.GeoType.MESH, newton.GeoType.CONVEX_MESH):
            try:
                mesh_id = sim.meshes.register(model.shape_source[shape])
                visual = {
                    "kind": "mesh",
                    "meshId": mesh_id,
                    "url": f"/api/v1/sessions/{session_id}/meshes/{mesh_id}",
                    "assetUpAxis": "Z",
                    "scale": [float(x) for x in scales[shape]],
                    "localPose": _pose(transforms[shape]),
                }
            except (AttributeError, ValueError) as exc:
                warnings.append(
                    {
                        "code": "unsupported_shape",
                        "entityKey": identity.entity_key,
                        "message": f"Mesh export failed: {exc}",
                    }
                )
        if geometry is None and visual is None:
            geometry = {"type": "box", "halfExtents": [max(abs(float(x)), 0.05) for x in scales[shape]]}
            warnings.append(
                {
                    "code": "unsupported_shape",
                    "entityKey": identity.entity_key,
                    "message": f"{newton.GeoType(shape_types[shape]).name} rendered as a box placeholder",
                }
            )
        if visual is None:
            visual = {"kind": "primitive", "geometry": geometry, "localPose": _pose(transforms[shape])}
        if body == -1:
            key = identity.entity_key
            site_visual = shape in site_aliases.values()
            if site_visual:
                # The prim keeps its Site identity. Its original world-static visual belongs
                # to the actual parent prim, preserving the protocol's unique public keys.
                key = node_parent(key)
            node = static_nodes.get(key)
            if node is None:
                node = {
                    "entityKey": key, "kind": "static", "name": key.rsplit("/", 1)[-1],
                    "stableKey": identity.stable,
                    "initialPose": ({"p": [0, 0, 0], "q": [0, 0, 0, 1]}
                                    if site_visual else visual["localPose"]),
                    "visuals": [], "colliders": [], "debug": {"newtonShapeIndex": shape},
                }
                static_nodes[key] = node
                nodes.append(node)
            initial, world = node["initialPose"], visual["localPose"]
            local = wp.transform_multiply(
                wp.transform_inverse(wp.transform(tuple(initial["p"]), tuple(initial["q"]))),
                wp.transform(tuple(world["p"]), tuple(world["q"])),
            )
            visual = {**visual, "localPose": _pose(local)}
        else:
            node = body_nodes[body]
        if shape_flags[shape] & int(newton.ShapeFlags.VISIBLE):
            node["visuals"].append(visual)
        if shape_flags[shape] & int(newton.ShapeFlags.COLLIDE_SHAPES | newton.ShapeFlags.COLLIDE_PARTICLES):
            node["colliders"].append(visual)

    for body, visuals in getattr(builder, "spatial_visuals", {}).items():
        node = body_nodes[body]
        node["fallbackVisuals"], node["visuals"] = node["visuals"], visuals
    for key, visuals in getattr(builder, "spatial_static_visuals", {}).items():
        node = static_nodes[key]
        node["fallbackVisuals"], node["visuals"] = node["visuals"], visuals
    nodes.extend(getattr(builder, "spatial_dressing", ()))

    # Logical hierarchy comes from label paths, independently of body transforms.
    keys = {n["entityKey"] for n in nodes}
    groups = {}
    for node in list(nodes):
        parent = node_parent(node["entityKey"])
        node["parentEntityKey"] = parent or None
        while parent and parent not in keys and parent not in groups:
            name = parent.rsplit("/", 1)[-1]
            grandparent = node_parent(parent)
            groups[parent] = {
                "entityKey": parent,
                "kind": "group",
                "name": name,
                "parentEntityKey": grandparent or None,
            }
            parent = grandparent

    joint_types = model.joint_type.numpy()
    parents, children = model.joint_parent.numpy(), model.joint_child.numpy()
    parent_poses = model.joint_X_p.numpy()
    dimensions, starts = model.joint_dof_dim.numpy(), model.joint_qd_start.numpy()
    axes = model.joint_axis.numpy()
    joints = []
    for index, identity in enumerate(labels.joints):
        kind = newton.JointType(joint_types[index]).name.lower()
        info = {
            "entityKey": identity.entity_key,
            "type": kind,
            "stableKey": identity.stable,
            "parentBodyKey": labels.bodies[int(parents[index])].entity_key if parents[index] >= 0 else None,
            "childBodyKey": labels.bodies[int(children[index])].entity_key,
            "dofCount": int(sum(dimensions[index])),
        }
        if kind != "free":
            info["parentLocalPose"] = _pose(parent_poses[index])
            info["anchorSiteKey"] = labels.shapes[sim.anchor_sites[index]].entity_key
        if kind in ("revolute", "prismatic"):
            info["axis"] = [float(v) for v in axes[starts[index]]]
        joints.append(info)

    warnings.extend(getattr(sim.builder, "spatial_warnings", ()) if hasattr(sim, "builder") else ())
    apply_bindings(nodes, sim.scene_id, warnings)
    return SceneManifest.model_validate(
        {
            "sessionId": session_id,
            "sceneRevision": scene_revision,
            "revisionNumericId": revision_numeric_id,
            "epoch": sim.epoch,
            "sceneName": sim.scene_id,
            "source": {
                "kind": "newton",
                "newtonVersion": version("newton"),
                "solver": type(sim.solver).__name__,
                "physicsDt": sim.physics_dt,
                "basePublishHz": publish_hz,
                "realtimeFactor": realtime_factor,
                "shapeCount": int(model.shape_count),
            },
            "nodes": [*groups.values(), *nodes],
            "sites": sites,
            "joints": joints,
            "warnings": warnings,
        }
    )
