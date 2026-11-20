"""Bake source frames and calibrated inertial/kinematic values into OpenUSD and GLB."""

import hashlib
import math

import numpy as np
import trimesh
from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdPhysics, UsdShade
from trimesh import transformations as tf

from apps.node.usd_mjcf import (
    JOINT_ARMATURE_ATTR,
    JOINT_PASSIVE_DAMPING_ATTR,
    JOINT_TARGET_RANGE_ATTR,
    check_training_structure,
    has_solid_components,
)
from packages.library.archive import invalid
from packages.library.visuals import glb_budget


def identifiers(names):
    result, used = {}, set()
    for name in names:
        clean = Tf.MakeValidIdentifier(name.replace("-", "_"))
        candidate = clean
        suffix = 1
        while candidate in used:
            suffix += 1
            candidate = f"{clean}_{suffix}"
        result[name] = candidate
        used.add(candidate)
    return result


def rotation(quat):
    return Gf.Quatf(float(quat[0]), Gf.Vec3f(*map(float, quat[1:])))


def translation(position):
    return Gf.Vec3f(*map(float, position))


def xform(prim, pose):
    UsdGeom.Xformable(prim).AddTransformOp().Set(Gf.Matrix4d(*pose.T.ravel().tolist()))


def custom(prim, name, value):
    prim.CreateAttribute(name, Sdf.ValueTypeNames.Float, custom=True).Set(float(value))


def solid_mesh(shape, path):
    mesh = shape.collision_hull.copy() if shape.convex else shape.mesh.copy()
    if not shape.convex:
        mesh.merge_vertices()
    try:
        if not mesh.is_volume or (not shape.convex and not has_solid_components(
            [tuple(point) for point in mesh.vertices.tolist()], [3] * len(mesh.faces),
            mesh.faces.ravel().tolist(), positive_volume=True,
        )):
            raise ValueError("Collision mesh must contain closed, positive-volume components")
    except (ValueError, ImportError) as exc:
        raise invalid(str(exc), path=path) from exc
    return mesh


def collision_mass(shape, length, path):
    if shape.kind == "mesh":
        mesh = solid_mesh(shape, path)
        mesh.apply_transform(shape.pose)
        mesh.apply_scale(length)
        return mesh.mass_properties

    size = np.asarray(shape.size) * length
    if not np.isfinite(size).all() or np.any(size <= 0):
        raise invalid("Geometry dimensions must be positive and finite", path=path)
    match shape.kind:
        case "box":
            volume = float(np.prod(size))
            squared = size**2
            inertia = np.diag(volume / 12 * (np.roll(squared, 1) + np.roll(squared, -1)))
        case "sphere":
            radius, = size
            volume = 4 / 3 * math.pi * radius**3
            inertia = trimesh.inertia.sphere_inertia(volume, radius)
        case "cylinder" | "capsule":
            radius, height = size
            volume = math.pi * radius**2 * height
            inertia = trimesh.inertia.cylinder_inertia(volume, radius, height)
            if shape.kind == "capsule":
                cap_volume = 4 / 3 * math.pi * radius**3
                inertia += trimesh.inertia.sphere_inertia(cap_volume, radius)
                # Translate the two hemispheres by half the cylindrical length.
                transverse_shift = cap_volume * (height**2 / 4 + 3 * radius * height / 8)
                inertia += np.diag((transverse_shift, transverse_shift, 0.))
                volume += cap_volume
        case _:
            raise invalid(f"Unsupported collision geometry {shape.kind}", path=path)
    basis = shape.pose[:3, :3]
    return trimesh.triangles.MassProperties(
        density=1., mass=volume, volume=volume, center_mass=shape.pose[:3, 3] * length,
        inertia=basis @ inertia @ basis.T,
    )


def usd_diagonal_inertia(inertia):
    # OpenUSD keeps the inferred moment order when principalAxes is authored alone.
    # Use its mass callback with the already-aggregated tensor at unit density.
    stage = Usd.Stage.CreateInMemory()
    body = UsdGeom.Xform.Define(stage, "/Body").GetPrim()
    rigid = UsdPhysics.RigidBodyAPI.Apply(body)
    UsdPhysics.MassAPI.Apply(body).CreateDensityAttr(1.)
    UsdPhysics.CollisionAPI.Apply(UsdGeom.Cube.Define(stage, "/Body/Shape").GetPrim())
    properties = UsdPhysics.RigidBodyAPI.MassInformation()
    properties.volume = 1.
    properties.centerOfMass = Gf.Vec3f(0.)
    properties.inertia = Gf.Matrix3f(inertia.tolist())
    properties.localPos = Gf.Vec3f(0.)
    properties.localRot = Gf.Quatf(1.)
    _, diagonal, _, _ = rigid.ComputeMassProperties(lambda _: properties)
    return diagonal


def inertial(body, density, length, kilograms, path):
    mass = body.mass * kilograms if body.mass is not None else None
    center = body.center * length if body.center is not None else None
    inertia = body.inertia * kilograms * length**2 if body.inertia is not None else None
    estimated = mass is None or inertia is None
    if estimated or center is None:
        bulk_density = body.density if body.density is not None else density
        parts = []
        for shape in body.shapes:
            if shape.collision:
                weight = shape.density / bulk_density if shape.density is not None else 1.
                parts.append((collision_mass(shape, length, path), weight))
        # Relative density retains the uniform-density calculation when there are no overrides.
        volume = sum(part.volume * weight for part, weight in parts)
        if estimated and volume <= 0:
            raise invalid("Missing inertia requires volumetric collision geometry", path=path)
        if center is None and volume > 0:
            center = sum((weight * part.volume * part.center_mass for part, weight in parts),
                         np.zeros(3)) / volume
        if mass is None:
            mass = bulk_density * volume
        if inertia is None:
            inertia = np.zeros((3, 3))
            for part, weight in parts:
                offset = part.center_mass - center
                shift = part.mass * (offset @ offset * np.eye(3) - np.outer(offset, offset))
                inertia += weight * (part.inertia + shift)
            inertia *= mass / volume
    if center is None:
        center = np.zeros(3)
    if not np.isfinite(mass) or mass <= 0 or not np.isfinite(inertia).all():
        raise invalid("Mass and inertia must be finite and positive", path=path)
    diagonal, axes = np.linalg.eigh(inertia)
    if diagonal[0] <= 0 or diagonal[2] > diagonal[0] + diagonal[1] + 1e-10:
        raise invalid("Inertia is not physically realizable", path=path)
    if body.inferred_principal_axes is not None:
        return mass, center, usd_diagonal_inertia(inertia), body.inferred_principal_axes, estimated
    # An already diagonal tensor needs no principal-frame rotation. This
    # avoids round-off from reconstructing exact zeros with Float32 quaternions.
    body_diagonal = np.diag(inertia)
    if np.max(np.abs(inertia - np.diag(body_diagonal))) <= diagonal[2] * 1e-14:
        return mass, center, body_diagonal, (1., 0., 0., 0.), estimated
    # Anchor each eigenspace to body axes. Degenerate moments must not make
    # the exported frame depend on the eigensolver's choice of a basis.
    start = 0
    while start < 3:
        end = start + 1
        while end < 3 and math.isclose(diagonal[start], diagonal[end], rel_tol=1e-10):
            end += 1
        basis = axes[:, start:end]
        projector = basis @ basis.T
        stable = []
        for value in projector.T:
            vector = value - sum((old * (value @ old) for old in stable), np.zeros(3))
            if (length_v := np.linalg.norm(vector)) > 1e-8:
                stable.append(vector / length_v)
            if len(stable) == end - start:
                break
        axes[:, start:end] = np.column_stack(stable)
        start = end
    if np.linalg.det(axes) < 0:
        axes[:, -1] *= -1
    frame = np.eye(4)
    frame[:3, :3] = axes
    return mass, center, diagonal, tf.quaternion_from_matrix(frame), estimated


def usd_shape(stage, path, shape, length):
    size = [value * length for value in shape.size]
    if any(not math.isfinite(value) or value <= 0 for value in size):
        raise invalid("Geometry dimensions must be positive and finite", path=path)
    if shape.kind == "box":
        geometry = UsdGeom.Cube.Define(stage, path)
        geometry.CreateSizeAttr(2.)
        transform = shape.pose.copy()
        transform[:3, 3] *= length
        transform[:3, :3] *= np.array(size) / 2.
    else:
        transform = shape.pose.copy()
        transform[:3, 3] *= length
        if shape.kind == "mesh":
            geometry = UsdGeom.Mesh.Define(stage, path)
            mesh = solid_mesh(shape, path) if shape.collision else shape.mesh
            points = mesh.vertices * length
            geometry.CreatePointsAttr(points.tolist())
            geometry.CreateExtentAttr([points.min(axis=0).tolist(), points.max(axis=0).tolist()])
            geometry.CreateFaceVertexCountsAttr([3] * len(mesh.faces))
            geometry.CreateFaceVertexIndicesAttr(mesh.faces.ravel().tolist())
            geometry.CreateSubdivisionSchemeAttr("none")
            if shape.collision:
                UsdPhysics.MeshCollisionAPI.Apply(geometry.GetPrim()).CreateApproximationAttr(
                    "convexHull" if shape.convex else "none",
                )
        else:
            schema = {"sphere": UsdGeom.Sphere, "cylinder": UsdGeom.Cylinder, "capsule": UsdGeom.Capsule}
            geometry = schema[shape.kind].Define(stage, path)
            geometry.CreateRadiusAttr(size[0])
            if shape.kind != "sphere":
                geometry.CreateHeightAttr(size[1])
                geometry.CreateAxisAttr("Z")
    xform(geometry.GetPrim(), transform)
    geometry.CreateDisplayColorAttr([translation(shape.color[:3])])
    geometry.CreateDisplayOpacityAttr([float(shape.color[3])])
    if shape.collision:
        UsdPhysics.CollisionAPI.Apply(geometry.GetPrim())
    if not shape.visual:
        geometry.CreatePurposeAttr("proxy")
    return geometry.GetPrim()


def scaling_pose(pose, length):
    result = pose.copy()
    result[:3, 3] *= length
    return result


def bake(robot, recipe, output):
    ordered = robot.ordered()
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.)
    root = UsdGeom.Xform.Define(stage, "/Asset").GetPrim()
    stage.SetDefaultPrim(root)
    UsdPhysics.ArticulationRootAPI.Apply(root)
    for scope in ("links", "joints", "materials"):
        UsdGeom.Scope.Define(stage, "/Asset/" + scope)
    body_names = identifiers(robot.bodies)
    joint_names = identifiers(joint.name for joint in robot.joints)
    world_anchor = "__world_anchor"
    while world_anchor in joint_names.values():
        world_anchor += "_"
    body_path = {name: "/Asset/links/" + identifier for name, identifier in body_names.items()}
    scalar = {joint.name for joint in robot.joints if joint.kind != "fixed"}
    if unknown := recipe.joints.keys() - scalar:
        raise invalid(f"Unknown scalar joints {sorted(unknown)}", path="calibration.joints")
    for site in recipe.sites.values():
        if site.body not in body_path:
            raise invalid("Site body does not name a source link", path=site.body)
    up = recipe.upAxis or robot.up
    upright = np.eye(4) if up == "Z" else tf.rotation_matrix(math.pi / 2, [1., 0., 0.])
    base = tf.euler_matrix(*map(math.radians, recipe.rpyDegrees)) @ upright
    base[:3, 3] = recipe.translationM
    source_meters = recipe.metersPerUnit or 1.
    length = source_meters * recipe.scale
    kilograms = recipe.scale**3
    mapping = {"schema": "lw.joint-calibration/1", "baseTransform": base.ravel().tolist(), "joints": []}
    visual = trimesh.Scene()
    materials, world, rest, estimated = {}, {}, {}, []
    triangle_count = 0
    total_mass = 0.
    fixed_base = robot.fixed if recipe.fixedBase is None else recipe.fixedBase
    for body, joint in ordered:
        local = scaling_pose(body.pose, length)
        child_frame, motion = np.eye(4), np.eye(4)
        parameters = None
        if joint is None:
            local = base @ local
        elif joint.kind != "fixed":
            parameters = recipe.joints.get(joint.name)
            axis = joint.axis / np.linalg.norm(joint.axis)
            if not np.isfinite(axis).all():
                raise invalid("Joint axis must be nonzero and finite", path=joint.name)
            positive_axis = 1 if axis[np.argmax(abs(axis))] > 0 else -1
            sign = parameters.sign if parameters and parameters.sign else positive_axis
            offset = parameters.zeroOffset if parameters else 0.
            unit_scale = source_meters if joint.kind == "slide" else 1.
            source_zero = -sign * offset
            child_frame = trimesh.geometry.align_vectors([1., 0., 0.], sign * axis)
            child_frame[:3, 3] = joint.anchor * length
            local = shift(local, child_frame, joint.kind, sign * (source_zero - joint.reference * unit_scale))
            initial = sign * joint.initial * unit_scale + offset
            if parameters and parameters.defaultPosition is not None:
                initial = parameters.defaultPosition
            limits = None
            if joint.limits:
                limits = sorted(sign * value * unit_scale + offset for value in joint.limits)
            valid_limits = limits is None or (
                np.isfinite(limits).all() and limits[0] < limits[1] and limits[0] <= initial <= limits[1]
            )
            if not math.isfinite(initial) or not valid_limits:
                raise invalid("Default posture must lie inside finite nonempty joint limits", path=joint.name)
            motion = shift(np.eye(4), child_frame, joint.kind, initial)
            mapping["joints"].append({
                "jointKey": "/Asset/joints/" + joint_names[joint.name], "sourceName": joint.name,
                "sign": sign, "zeroOffset": offset, "unit": "m" if joint.kind == "slide" else "rad",
            })

        world[body.name] = world[joint.parent] @ local if joint else local
        rest[body.name] = rest[joint.parent] @ local @ motion if joint else local
        path = body_path[body.name]
        prim = UsdGeom.Xform.Define(stage, path).GetPrim()
        xform(prim, world[body.name])
        UsdPhysics.RigidBodyAPI.Apply(prim)
        api = UsdPhysics.MassAPI.Apply(prim)
        mass, center, moments, axes, inferred = inertial(body, recipe.density, length, kilograms, path)
        api.CreateMassAttr(mass)
        api.CreateCenterOfMassAttr(translation(center))
        api.CreateDiagonalInertiaAttr(translation(moments))
        api.CreatePrincipalAxesAttr(rotation(axes))
        total_mass += mass
        if inferred or body.estimated_inertia:
            estimated.append(path)
        if joint:
            joint_path = "/Asset/joints/" + joint_names[joint.name]
            authored = write_joint(stage, joint_path, joint, body_path, local @ child_frame, child_frame)
            if joint.kind != "fixed":
                write_drive(authored, joint, limits, initial, parameters)
                if joint.target_range is not None:
                    lower, upper = sorted(sign * value * unit_scale + offset for value in joint.target_range)
                    authored.GetPrim().CreateAttribute(
                        JOINT_TARGET_RANGE_ATTR, Sdf.ValueTypeNames.Double2, custom=True,
                    ).Set(Gf.Vec2d(lower, upper))
        elif fixed_base:
            fixed = UsdPhysics.FixedJoint.Define(stage, "/Asset/joints/" + world_anchor)
            fixed.CreateBody1Rel().SetTargets([path])
            fixed.CreateLocalPos0Attr(translation(local[:3, 3]))
            fixed.CreateLocalRot0Attr(rotation(tf.quaternion_from_matrix(local)))

        has_visual = any(shape.visual for shape in body.shapes)
        for index, shape in enumerate(body.shapes):
            geom_path = f"{path}/geom_{index}"
            geom = usd_shape(stage, geom_path, shape, length)
            if shape.collision:
                friction = shape.friction if recipe.friction is None else recipe.friction
                if not math.isfinite(friction) or friction < 0:
                    raise invalid("Contact friction must be finite and nonnegative", path=geom_path)
                contact = friction, shape.restitution
                if contact not in materials:
                    material = UsdShade.Material.Define(stage, f"/Asset/materials/contact_{len(materials)}")
                    physics = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
                    physics.CreateDynamicFrictionAttr(friction)
                    physics.CreateStaticFrictionAttr(friction)
                    if shape.restitution:
                        physics.CreateRestitutionAttr(shape.restitution)
                    materials[contact] = material
                UsdShade.MaterialBindingAPI.Apply(geom).Bind(materials[contact], materialPurpose="physics")
            if shape.visual or not has_visual:
                mesh = shape.surface()
                mesh.apply_scale(length)
                rgba = np.rint(np.array(shape.color).clip(0, 1) * 255).astype(np.uint8)
                payload = mesh.vertices.tobytes() + mesh.faces.tobytes() + rgba.tobytes()
                digest = hashlib.sha256(payload).hexdigest()
                if digest not in visual.geometry:
                    triangle_count += len(mesh.faces)
                    if triangle_count > 500000:
                        raise invalid("Asset Visual exceeds 500000 triangles", path=geom_path)
                    # GLB stores vertex colors. Keep unreferenced vertices zero
                    # as in the uniform face-to-vertex conversion.
                    colors = np.zeros((len(mesh.vertices), 4), dtype=np.uint8)
                    colors[mesh.faces.ravel()] = rgba
                    mesh.visual.vertex_colors = colors
                    visual.geometry[digest] = mesh
                # glTF is Y-up, while the stage remains Z-up.
                pose = tf.rotation_matrix(-math.pi / 2, [1., 0., 0.]) @ rest[body.name]
                visual.graph.update(frame_to=f"{body_names[body.name]}_{index}", geometry=digest,
                                    matrix=pose @ scaling_pose(shape.pose, length))
        sites = {name: scaling_pose(pose, length) for name, pose in body.sites.items()}
        for name, site in recipe.sites.items():
            if site.body == body.name:
                pose = tf.euler_matrix(*map(math.radians, site.rpyDegrees))
                pose[:3, 3] = site.positionM
                sites[name] = pose
        for name, identifier in identifiers(sites).items():
            marker = UsdGeom.Xform.Define(stage, f"{path}/{identifier}").GetPrim()
            if marker.HasAPI(UsdPhysics.CollisionAPI) or marker.IsA(UsdGeom.Gprim):
                raise invalid("Site name conflicts with generated geometry", path=str(marker.GetPath()))
            xform(marker, sites[name])

    exclusions = robot.exclusions | {tuple(sorted(pair)) for pair in recipe.excludePairs}
    for first, second in sorted(exclusions):
        if first not in body_path or second not in body_path or first == second:
            raise invalid("Exclusion must name two distinct source links", path="calibration.excludePairs")
        api = UsdPhysics.FilteredPairsAPI.Apply(stage.GetPrimAtPath(body_path[first]))
        api.CreateFilteredPairsRel().AddTarget(body_path[second])
    check_training_structure(stage, root.GetPath(), list(stage.Traverse()))
    if not visual.geometry:
        raise invalid("Robot has no supported visual or collision geometry", path="visual.glb")
    stage.GetRootLayer().Export(str(output / "asset.usda"))
    (output / "visual.glb").write_bytes(visual.export(file_type="glb"))
    glb_budget(output / "visual.glb")
    return mapping, estimated, float(total_mass)


def shift(pose, frame, kind, delta):
    move = np.eye(4)
    if kind == "hinge":
        move = tf.rotation_matrix(delta, [1., 0., 0.])
    else:
        move[0, 3] = delta
    return pose @ frame @ move @ np.linalg.inv(frame)


def write_joint(stage, path, joint, bodies, parent, child):
    schema = {"hinge": UsdPhysics.RevoluteJoint, "slide": UsdPhysics.PrismaticJoint,
              "fixed": UsdPhysics.FixedJoint}
    result = schema[joint.kind].Define(stage, path)
    result.CreateBody0Rel().SetTargets([bodies[joint.parent]])
    result.CreateBody1Rel().SetTargets([bodies[joint.child]])
    result.CreateLocalPos0Attr(translation(parent[:3, 3]))
    result.CreateLocalPos1Attr(translation(child[:3, 3]))
    result.CreateLocalRot0Attr(rotation(tf.quaternion_from_matrix(parent)))
    result.CreateLocalRot1Attr(rotation(tf.quaternion_from_matrix(child)))
    result.CreateCollisionEnabledAttr(True)
    return result


def write_drive(authored, joint, limits, initial, calibration):
    angular = joint.kind == "hinge"
    ratio = 180. / math.pi if angular else 1.
    channel = "angular" if angular else "linear"
    authored.CreateAxisAttr("X")
    if limits is not None:
        authored.CreateLowerLimitAttr(limits[0] * ratio)
        authored.CreateUpperLimitAttr(limits[1] * ratio)
    prim = authored.GetPrim()
    custom(prim, f"state:{channel}:physics:position", initial * ratio)
    armature = calibration.armature if calibration and calibration.armature is not None else joint.armature
    custom(prim, JOINT_ARMATURE_ATTR, armature)
    custom(prim, "newton:friction", joint.frictionloss)
    if joint.passive_damping:
        custom(prim, JOINT_PASSIVE_DAMPING_ATTR, joint.passive_damping / ratio)
    drive = UsdPhysics.DriveAPI.Apply(prim, channel)
    drive.CreateTypeAttr("force")
    drive.CreateTargetPositionAttr(initial * ratio)
    kp = calibration.kp if calibration and calibration.kp is not None else joint.kp
    kv = calibration.kv if calibration and calibration.kv is not None else joint.kv
    if joint.target_range is not None and kp <= 0:
        raise invalid("Position control range requires positive stiffness", path=joint.name)
    drive.CreateStiffnessAttr(kp / ratio)
    drive.CreateDampingAttr(kv / ratio)
    force = calibration.maxForce if calibration and calibration.maxForce is not None else joint.force
    if force is not None:
        drive.CreateMaxForceAttr(force)
