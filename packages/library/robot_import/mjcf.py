"""Read MuJoCo's compiled rigid-body representation after archive preflight."""

import numpy as np
import trimesh

from apps.node.usd_mjcf import mujoco_contact_pairs, mujoco_link_inertia
from packages.library.archive import invalid
from packages.library.common import LibraryError
from packages.library.robot_import.model import Body, Joint, Robot, Shape, transform


def load(source, entry, standing_key):
    xml, assets = source.mjcf(entry)
    return compiled(xml, entry, assets=assets, standing_key=standing_key)


def compiled(xml, entry, *, assets=None, standing_key=None, usd_colliders=None):
    try:
        import mujoco
    except ImportError as exc:
        raise LibraryError(
            "robot_import_unavailable", "Robot import requires MuJoCo (simulation extra)", 503,
        ) from exc
    try:
        model = mujoco.MjModel.from_xml_string(xml, assets or {})
    except ValueError as exc:
        raise invalid(str(exc), path=entry) from exc
    return from_model(model, entry, standing_key=standing_key, usd_colliders=usd_colliders or {})


def geom_shape(model, index, path):
    import mujoco

    kind = mujoco.mjtGeom(int(model.geom_type[index])).name.removeprefix("mjGEOM_").lower()
    size = model.geom_size[index]
    collision = bool(model.geom_contype[index] or model.geom_conaffinity[index])
    pose = transform(model.geom_pos[index], model.geom_quat[index])
    mesh = None
    if kind == "mesh":
        mesh_id = model.geom_dataid[index]
        vertex, count = model.mesh_vertadr[mesh_id], model.mesh_vertnum[mesh_id]
        face, nface = model.mesh_faceadr[mesh_id], model.mesh_facenum[mesh_id]
        mesh = trimesh.Trimesh(model.mesh_vert[vertex:vertex + count], model.mesh_face[face:face + nface],
                               process=False)
        size = ()
    elif kind in ("box", "cylinder", "capsule", "sphere"):
        size = {"box": 2 * size, "sphere": size[:1], "cylinder": size[:2] * [1, 2],
                "capsule": size[:2] * [1, 2]}[kind]
    else:
        raise invalid(f"Unsupported robot geom type {kind}", path=path)
    material = model.geom_matid[index]
    if material >= 0 and model.mat_texid[material].max(initial=-1) >= 0:
        raise invalid("Textured MJCF robot geoms are outside the supported visual subset", path=path)
    color = model.mat_rgba[material] if material >= 0 else model.geom_rgba[index]
    return Shape(
        kind, tuple(size), pose, collision, not collision or model.geom_group[index] < 3,
        tuple(color), float(model.geom_friction[index, 0]), mesh, convex=collision and mesh is not None,
    )


def from_model(model, entry, *, standing_key, usd_colliders):
    import mujoco

    if model.ntendon or model.neq or model.nflex or model.npair:
        raise invalid("Unsupported coupled, deformable or explicit-pair MJCF physics", path=entry)
    if model.nbody > 129 or model.ngeom > 4096:
        raise invalid("Compiled MJCF exceeds the robot size budget", path=entry)
    names = {index: model.body(index).name or f"link_{index}" for index in range(1, model.nbody)}
    roots = [index for index in names if model.body_parentid[index] == 0]
    if len(roots) != 1:
        raise invalid("Expected exactly one MJCF robot root", path=entry)
    for index in range(model.ngeom):
        if model.geom_bodyid[index] == 0 and model.geom_type[index] != mujoco.mjtGeom.mjGEOM_PLANE:
            raise invalid("An MJCF robot archive can only include a world floor plane", path=entry)
    if standing_key is not None:
        identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, standing_key)
        if identifier < 0:
            raise invalid("Requested standing keyframe is missing", path=standing_key)
        posture = model.key_qpos[identifier]
    else:
        posture = model.qpos0
    actuators = {}
    for index in range(model.nu):
        path = model.actuator(index).name or f"actuator_{index}"
        joint = int(model.actuator_trnid[index, 0])
        if (model.actuator_trntype[index] != mujoco.mjtTrn.mjTRN_JOINT
                or model.actuator_dyntype[index] != mujoco.mjtDyn.mjDYN_NONE
                or model.actuator_gaintype[index] != mujoco.mjtGain.mjGAIN_FIXED
                or model.actuator_biastype[index] != mujoco.mjtBias.mjBIAS_AFFINE
                or model.actuator_gear[index, 0] != 1 or joint in actuators):
            raise invalid("Only one standard position drive per scalar joint is supported", path=path)
        gain = float(model.actuator_gainprm[index, 0])
        _, position, velocity = model.actuator_biasprm[index, :3]
        if not np.isclose(gain, -position) or min(gain, -velocity) < 0:
            raise invalid("Actuator is not a standard position drive", path=path)
        force = None
        if model.actuator_forcelimited[index]:
            lower, upper = model.actuator_forcerange[index]
            if lower != -upper:
                raise invalid("Actuator force limits must be symmetric", path=path)
            force = float(upper)
        actuators[joint] = gain, float(-velocity), force

    bodies, joints, colliders = {}, [], {}
    fixed = True
    for index, name in names.items():
        body = bodies[name] = Body(name, transform(model.body_pos[index], model.body_quat[index]))
        body.mass = float(model.body_mass[index]) or None
        if body.mass is not None:
            body.center = model.body_ipos[index].copy()
            body.inertia = mujoco_link_inertia(model, index)
        for geom in range(model.body_geomadr[index], model.body_geomadr[index] + model.body_geomnum[index]):
            geom_name = model.geom(geom).name
            shape = geom_shape(model, geom, f"{name}/{geom_name or geom}")
            if geom_name in usd_colliders:
                shape.convex, shape.restitution = usd_colliders[geom_name]
            body.shapes.append(shape)
            if shape.collision:
                colliders.setdefault(name, []).append(geom)
        for site in np.flatnonzero(model.site_bodyid == index):
            path = model.site(int(site)).name
            if path:
                body.sites[path] = transform(model.site_pos[site], model.site_quat[site])
        parent = int(model.body_parentid[index])
        count, joint_id = model.body_jntnum[index], model.body_jntadr[index]
        if count > 1 or model.body_mocapid[index] >= 0:
            raise invalid("Only one scalar degree of freedom per link is supported", path=name)
        if parent == 0:
            if count and model.jnt_type[joint_id] != mujoco.mjtJoint.mjJNT_FREE:
                raise invalid("Root must be free or fixed to the world", path=name)
            fixed = not count
            if count:
                start = model.jnt_qposadr[joint_id]
                body.pose = transform(posture[start:start + 3], posture[start + 3:start + 7])
            continue
        if not count:
            joints.append(Joint(f"weld_{index}", "fixed", names[parent], name))
            continue
        joint_name = model.joint(joint_id).name
        kinds = {mujoco.mjtJoint.mjJNT_HINGE: "hinge", mujoco.mjtJoint.mjJNT_SLIDE: "slide"}
        kind = kinds.get(int(model.jnt_type[joint_id]))
        if kind is None or not joint_name:
            raise invalid("Unsupported or unnamed MJCF scalar joint", path=joint_name or name)
        address, dof = model.jnt_qposadr[joint_id], model.jnt_dofadr[joint_id]
        if model.jnt_margin[joint_id] != 0:
            raise invalid("MJCF limit activation margin is outside the canonical USD subset", path=joint_name)
        joint = Joint(
            joint_name, kind, names[parent], name, model.jnt_axis[joint_id], model.jnt_pos[joint_id],
        )
        joint.reference = float(model.qpos0[address])
        joint.initial = float(posture[address])
        if model.jnt_limited[joint_id]:
            if model.opt.disableflags & mujoco.mjtDisableBit.mjDSBL_LIMIT:
                raise invalid("MJCF disables its authored hard limits", path=joint_name)
            joint.limits = tuple(model.jnt_range[joint_id])
        gain, damping, force = actuators.get(joint_id, (0., 0., None))
        joint.kp, joint.kv, joint.force = gain, damping + float(model.dof_damping[dof]), force
        joint.armature = float(model.dof_armature[dof])
        joint.frictionloss = float(model.dof_frictionloss[dof])
        joints.append(joint)

    pairs = mujoco_contact_pairs(model)
    exclusions = set()
    for first in colliders:
        for second in colliders:
            if first >= second:
                continue
            permitted = {
                tuple(sorted((a, b))) in pairs
                for a in colliders[first] for b in colliders[second]
            }
            if len(permitted) != 1:
                raise invalid("Mixed collider masks cannot be represented by a link-pair exclusion",
                              path=f"{first}/{second}")
            if not permitted.pop():
                exclusions.add((first, second))
    return Robot(bodies, joints, fixed, exclusions)
