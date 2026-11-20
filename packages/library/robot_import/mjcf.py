"""Read MuJoCo's compiled rigid-body representation after archive preflight."""

import xml.etree.ElementTree as ET

import numpy as np
import trimesh

from apps.node.usd_mjcf import mujoco_contact_pairs, mujoco_link_inertia
from packages.library.archive import invalid
from packages.library.common import LibraryError
from packages.library.robot_import.model import Body, Joint, Robot, Shape, transform


def load(source, entry, recipe):
    xml, assets = source.mjcf(entry)
    density = recipe.density * (recipe.metersPerUnit or 1.)**3
    return compiled(xml, entry, assets=assets, standing_key=recipe.standingKey, density=density)


def compiled(xml, entry, *, assets=None, standing_key=None, usd_colliders=None, density=None):
    try:
        import mujoco
    except ImportError as exc:
        raise LibraryError(
            "robot_import_unavailable", "Robot import requires MuJoCo (simulation extra)", 503,
        ) from exc
    try:
        if density is None:
            model = mujoco.MjModel.from_xml_string(xml, assets or {})
            estimated = set()
        else:
            model, estimated = compile_with_density(mujoco, xml, assets, density)
    except ValueError as exc:
        raise invalid(str(exc), path=entry) from exc
    return from_model(
        model, entry, standing_key=standing_key, usd_colliders=usd_colliders, estimated=estimated,
    )


def compile_with_density(mujoco, xml, assets, density):
    document = ET.fromstring(xml)
    defaults = document.find("default")
    if defaults is None:
        defaults = ET.SubElement(document, "default")
    geom_default = defaults.find("geom")
    if geom_default is None:
        geom_default = ET.SubElement(defaults, "geom")

    marker = None
    if "density" not in geom_default.attrib:
        # Use a distinct default in the parser to trace implicit density
        # through native class and childclass inheritance, including 1000.
        authored = {float(node.get("density")) for node in document.iter("geom") if "density" in node.attrib}
        marker = 1001.
        while marker in authored:
            marker += 1.
        geom_default.set("density", str(marker))

    spec = mujoco.MjSpec.from_string(ET.tostring(document, encoding="unicode"), assets=assets)
    low, high = spec.compiler.inertiagrouprange
    inference = spec.compiler.inertiafromgeom
    estimated = set()
    for index, body in enumerate(spec.bodies):
        inferred = index != 0 and (
            inference == mujoco.mjtInertiaFromGeom.mjINERTIAFROMGEOM_TRUE
            or (inference == mujoco.mjtInertiaFromGeom.mjINERTIAFROMGEOM_AUTO and not body.explicitinertial)
        )
        if inferred:
            estimated.add(index)
        for geom in body.geoms:
            if marker is None or geom.density != marker:
                continue
            geom.density = 1000.
            if inferred and low <= geom.group <= high and np.isnan(geom.mass):
                # An implicit visual contributes no collision volume.
                geom.density = density if geom.contype or geom.conaffinity else 0.
    return spec.compile(), estimated


def geom_shape(model, index, path, usd_collider):
    import mujoco

    kind = mujoco.mjtGeom(int(model.geom_type[index])).name.removeprefix("mjGEOM_").lower()
    size = model.geom_size[index]
    collision = bool(model.geom_contype[index] or model.geom_conaffinity[index])
    pose = transform(model.geom_pos[index], model.geom_quat[index])
    mesh, hull = None, None
    use_hull, restitution, density = usd_collider if usd_collider is not None else (True, 0., None)
    convex = collision and kind == "mesh" and use_hull
    if kind == "mesh":
        mesh_id = model.geom_dataid[index]
        vertex, count = model.mesh_vertadr[mesh_id], model.mesh_vertnum[mesh_id]
        face, nface = model.mesh_faceadr[mesh_id], model.mesh_facenum[mesh_id]
        mesh = trimesh.Trimesh(model.mesh_vert[vertex:vertex + count], model.mesh_face[face:face + nface],
                               process=False)
        if convex:
            graph = model.mesh_graphadr[mesh_id]
            if graph < 0:
                raise invalid("MuJoCo did not compile a convex collision hull", path=path)
            hull_vertices, hull_faces = model.mesh_graph[graph:graph + 2]
            # MuJoCo stores global vertex indices for the triangle faces at
            # the end of the graph (mjCMesh::MakeGraph in user_mesh.cc).
            start = graph + 2 + 3 * hull_vertices + 3 * hull_faces
            triangles = model.mesh_graph[start:start + 3 * hull_faces].reshape(-1, 3)
            vertex_ids, local = np.unique(triangles, return_inverse=True)
            hull = trimesh.Trimesh(mesh.vertices[vertex_ids], local.reshape(-1, 3), process=False)
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
        tuple(color), float(model.geom_friction[index, 0]), mesh, convex=convex,
        restitution=restitution, collision_hull=hull, density=density,
    )


def from_model(model, entry, *, standing_key, usd_colliders, estimated):
    import mujoco

    if model.ntendon or model.neq or model.nflex or model.npair:
        raise invalid("Unsupported coupled, deformable or explicit-pair MJCF physics", path=entry)
    disabled = model.opt.disableflags
    for flag, option in ((mujoco.mjtDisableBit.mjDSBL_CONSTRAINT, "constraint"),
                         (mujoco.mjtDisableBit.mjDSBL_CONTACT, "contact")):
        if disabled & flag:
            raise invalid(
                "Disabled MJCF option is outside the canonical USD subset", path=f"option/flag:{option}",
            )
    if model.opt.enableflags & mujoco.mjtEnableBit.mjENBL_OVERRIDE:
        raise invalid("MJCF contact override is unsupported", path="option/flag:override")
    if model.nbody > 129 or model.ngeom > 4096:
        raise invalid("Compiled MJCF exceeds the robot size budget", path=entry)
    names = {index: model.body(index).name or f"link_{index}" for index in range(1, model.nbody)}
    roots = [index for index in names if model.body_parentid[index] == 0]
    if len(roots) != 1:
        raise invalid("Expected exactly one MJCF robot root", path=entry)
    # USD-derived MJCF already encodes the canonical contact material, including restitution.
    defaults = mujoco.MjSpec().default.geom if usd_colliders is None else None
    usd_geometry = usd_colliders or {}
    for index in range(model.ngeom):
        body = int(model.geom_bodyid[index])
        if body == 0 and model.geom_type[index] != mujoco.mjtGeom.mjGEOM_PLANE:
            raise invalid("An MJCF robot archive can only include a world floor plane", path=entry)
        if defaults is None or not (model.geom_contype[index] or model.geom_conaffinity[index]):
            continue
        path = f"{names.get(body, 'world')}/{model.geom(index).name or str(index)}"
        dimension, priority = int(model.geom_condim[index]), int(model.geom_priority[index])
        if dimension != 3 or priority != 0:
            raise invalid(
                f"MJCF contact condim={dimension}, priority={priority} cannot map to USD sliding friction",
                path=path,
            )
        for field in ("margin", "gap", "solref", "solimp", "solmix"):
            value = getattr(model, f"geom_{field}")[index]
            if not np.array_equal(value, getattr(defaults, field)):
                raise invalid(f"MJCF contact {field} differs from the supported default", path=path)
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
        offset, position, velocity = model.actuator_biasprm[index, :3]
        if offset != 0. or not np.isclose(gain, -position) or min(gain, -velocity) < 0:
            raise invalid("Actuator is not a standard position drive", path=path)
        if model.actuator_damping[index] != 0 or np.any(model.actuator_dampingpoly[index]):
            raise invalid("Passive actuator transmission damping is unsupported", path=path)
        if model.opt.disableactuator & (1 << int(model.actuator_group[index])):
            raise invalid("MJCF disables the actuator group", path=path)
        force = None
        if model.actuator_forcelimited[index]:
            lower, upper = model.actuator_forcerange[index]
            if lower != -upper:
                raise invalid("Actuator force limits must be symmetric", path=path)
            force = float(upper)
        target_range = None
        clamp_enabled = not model.opt.disableflags & mujoco.mjtDisableBit.mjDSBL_CLAMPCTRL
        if model.actuator_ctrllimited[index] and clamp_enabled:
            lower, upper = model.actuator_ctrlrange[index]
            if not np.isfinite([lower, upper]).all() or not lower < upper:
                raise invalid("Position control range must have two ordered finite bounds", path=path)
            if gain <= 0:
                raise invalid("Position control range requires positive stiffness", path=path)
            target_range = (float(lower), float(upper))
        actuators[joint] = gain, float(-velocity), force, target_range

    bodies, joints, colliders = {}, [], {}
    fixed = True
    for index, name in names.items():
        if model.body_gravcomp[index] != 0.:
            raise invalid("MJCF body gravity compensation is outside the canonical USD subset", path=name)
        body = bodies[name] = Body(name, transform(model.body_pos[index], model.body_quat[index]))
        body.estimated_inertia = index in estimated
        body.mass = float(model.body_mass[index]) or None
        if body.mass is not None:
            body.center = model.body_ipos[index].copy()
            body.inertia = mujoco_link_inertia(model, index)
        for geom in range(model.body_geomadr[index], model.body_geomadr[index] + model.body_geomnum[index]):
            geom_name = model.geom(geom).name
            shape = geom_shape(model, geom, f"{name}/{geom_name or geom}", usd_geometry.get(geom_name))
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
                dof = model.jnt_dofadr[joint_id]
                free_dofs = slice(dof, dof + 6)
                if (joint_id in actuators or model.jnt_stiffness[joint_id] != 0
                        or np.any(model.jnt_stiffnesspoly[joint_id])
                        or np.any(model.dof_damping[free_dofs])
                        or np.any(model.dof_dampingpoly[free_dofs])
                        or np.any(model.dof_frictionloss[free_dofs])
                        or np.any(model.dof_armature[free_dofs])):
                    raise invalid(
                        "Floating root requires an undriven joint without passive forces or armature",
                        path=model.joint(joint_id).name or name,
                    )
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
        if model.jnt_stiffness[joint_id] != 0 or np.any(model.jnt_stiffnesspoly[joint_id]):
            raise invalid("MJCF joint springs are outside the canonical USD subset", path=joint_name)
        joint = Joint(
            joint_name, kind, names[parent], name, model.jnt_axis[joint_id], model.jnt_pos[joint_id],
        )
        joint.reference = float(model.qpos0[address])
        joint.initial = float(posture[address])
        if model.jnt_limited[joint_id]:
            if disabled & mujoco.mjtDisableBit.mjDSBL_LIMIT:
                raise invalid("MJCF disables its authored hard limits", path=joint_name)
            joint.limits = tuple(model.jnt_range[joint_id])
        gain, damping, force, target_range = actuators.get(joint_id, (0., 0., None, None))
        passive_damping = float(model.dof_damping[dof])
        if not np.isfinite(passive_damping) or passive_damping < 0 or np.any(model.dof_dampingpoly[dof]):
            raise invalid("Joint requires finite nonnegative linear passive damping", path=joint_name)
        if passive_damping and disabled & mujoco.mjtDisableBit.mjDSBL_DAMPER:
            raise invalid("MJCF disables authored joint damping", path=joint_name)
        if joint_id in actuators and disabled & mujoco.mjtDisableBit.mjDSBL_ACTUATION:
            raise invalid("MJCF disables the joint actuator", path=joint_name)
        if model.dof_frictionloss[dof] and disabled & mujoco.mjtDisableBit.mjDSBL_FRICTIONLOSS:
            raise invalid("MJCF disables authored joint friction loss", path=joint_name)
        if model.jnt_actfrclimited[joint_id]:
            lower, upper = model.jnt_actfrcrange[joint_id]
            if not np.isfinite([lower, upper]).all() or lower != -upper or upper <= 0:
                raise invalid("Joint actuator force limit requires a finite symmetric range", path=joint_name)
            if model.jnt_actgravcomp[joint_id]:
                raise invalid("Joint actuator force limit includes gravity compensation", path=joint_name)
            if joint_id in actuators:
                # With one unit-gear position drive, both constraints bound
                # the same effort. USD DriveAPI can express their intersection.
                force = float(upper) if force is None else min(force, float(upper))
        joint.kp, joint.kv, joint.force = gain, damping, force
        joint.target_range = target_range
        joint.passive_damping = passive_damping
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
