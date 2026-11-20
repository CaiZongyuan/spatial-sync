"""Compare the imported Newton model with the trainer's compiled MuJoCo model."""

from packages.library.physics_checks import check

REL_TOL = 1e-4
MASS_ATOL = 1e-6  # kg
INERTIA_ATOL = 1e-8  # kg m²
PARAMETER_ATOL = 1e-6  # Coordinates and drive/contact parameters in solver units
POSITION_ATOL = 1e-5  # m, accounting for Newton's Float32 transforms
ORIENTATION_ATOL = 1e-5  # Unit rotation basis vectors


def _named_pairs(pairs, names):
    return {tuple(sorted((names.get(a, f"geom {a}"), names.get(b, f"geom {b}")))) for a, b in pairs}


def compare_models(newton_model, derived, *, newton_state=None, newton_origin_z=0., mujoco_solver=None):
    import mujoco
    import newton
    import numpy as np

    from apps.node.usd_mjcf import (
        mujoco_collision_axes,
        mujoco_contact_pairs,
        mujoco_joint_limits,
        mujoco_link_inertia,
    )
    from apps.node.usd_mjcf import mujoco_rotation_matrix as rotation

    model = mujoco.MjModel.from_xml_string(derived.mjcf_xml)
    reference = newton_model
    if newton_state is None:
        newton_state = reference.state()
        newton.eval_fk(reference, newton_state.joint_q, newton_state.joint_qd, newton_state)
    newton_body_poses = newton_state.body_q.numpy()
    origin = np.array([0., 0., newton_origin_z])
    failures = []

    def equal(field, expected, actual, path=None):
        if expected != actual:
            failures.append(check("P7", "fail", f"{field} differs between Newton and MuJoCo", entity=path,
                                  measurements={"newton": expected, "mujoco": actual}))

    def close(field, expected, actual, path=None, atol=PARAMETER_ATOL, rtol=REL_TOL):
        left, right = np.asarray(expected, dtype=float), np.asarray(actual, dtype=float)
        if left.shape == right.shape and np.allclose(left, right, atol=atol, rtol=rtol):
            return
        if np.isfinite(left).all() and np.isfinite(right).all():
            equal(field, left.tolist(), right.tolist(), path)
        else:
            failures.append(check("P7", "fail", f"{field} has non-finite values", entity=path))

    def ids(names, kind):
        return {path: mujoco.mj_name2id(model, kind, name) for path, name in names.items()}

    joint_ids = ids({joint.prim_path: joint.name for joint in derived.joints}, mujoco.mjtObj.mjOBJ_JOINT)
    newton_body_rotations = [rotation(pose[[6, 3, 4, 5]]) for pose in newton_body_poses]
    data = mujoco.MjData(model)
    for joint in derived.joints:
        index = joint_ids[joint.prim_path]
        if index >= 0 and joint.initial_position is not None:
            data.qpos[model.jnt_qposadr[index]] = joint.initial_position
    mujoco.mj_forward(model, data)

    body_ids = ids(derived.body_names_by_path, mujoco.mjtObj.mjOBJ_BODY)
    newton_bodies = dict(zip(reference.body_label, range(reference.body_count), strict=True))
    equal("Rigid-body count", reference.body_count, model.nbody - 1)
    equal("Rigid-body Prim Paths", sorted(newton_bodies),
          sorted(path for path, idx in body_ids.items() if idx > 0))
    mass = reference.body_mass.numpy()
    centers = reference.body_com.numpy()
    inertia = reference.body_inertia.numpy()
    close("Total mass", float(mass.sum()), float(model.body_mass.sum()), atol=MASS_ATOL)
    for path, idx in body_ids.items():
        if path not in newton_bodies or idx < 1:
            continue
        ref = newton_bodies[path]
        pose = newton_body_poses[ref]
        close("Body world position", pose[:3] - origin, data.xpos[idx], path, POSITION_ATOL, 0.)
        close("Body world orientation", newton_body_rotations[ref],
              data.xmat[idx].reshape(3, 3), path, ORIENTATION_ATOL, 0.)
        close("Body mass", mass[ref], model.body_mass[idx], path, MASS_ATOL)
        # A body with no modeled mass has no physical COM. Fixed and mocap
        # links can still carry mass and use the same link-local comparison.
        if mass[ref] > 0. or model.body_mass[idx] > 0.:
            local_center = data.xmat[idx].reshape(3, 3).T @ (data.xipos[idx] - data.xpos[idx])
            close("Body center of mass in the link frame", centers[ref], local_center,
                  path, POSITION_ATOL, 0.)
        close("Body inertia in the link frame", inertia[ref],
              mujoco_link_inertia(model, idx), path, INERTIA_ATOL)

    joint_types = {
        newton.JointType.PRISMATIC: mujoco.mjtJoint.mjJNT_SLIDE,
        newton.JointType.REVOLUTE: mujoco.mjtJoint.mjJNT_HINGE,
    }
    joint_kind = reference.joint_type.numpy()
    newton_joints = {path: i for i, path in enumerate(reference.joint_label) if joint_kind[i] in joint_types}
    actuator_by_joint = {int(row[0]): index for index, row in enumerate(model.actuator_trnid)}
    dof_starts = reference.joint_qd_start.numpy()
    ke, kd = reference.joint_target_ke.numpy(), reference.joint_target_kd.numpy()
    expected_order = [path for path, ref in newton_joints.items() if
                      ke[dof_starts[ref]] > 0 or kd[dof_starts[ref]] > 0]
    path_by_joint_id = {idx: path for path, idx in joint_ids.items() if idx >= 0}
    q_starts = reference.joint_q_start.numpy()
    scalar_kinds = {int(kind) for kind in joint_types.values()}
    scalar_ids = [idx for idx, kind in enumerate(model.jnt_type) if kind in scalar_kinds]
    scalar_ids.sort(key=lambda idx: model.jnt_qposadr[idx])
    expected_scalar_order = sorted(newton_joints, key=lambda path: q_starts[newton_joints[path]])
    equal("Scalar joint coordinate order", expected_scalar_order,
          [path_by_joint_id.get(idx, model.joint(idx).name) for idx in scalar_ids])
    actual_order = [path_by_joint_id.get(int(idx), f"joint index {idx}")
                    for idx in model.actuator_trnid[:, 0]]
    equal("Controlled joint order", expected_order, actual_order)
    equal("Scalar joint Prim Paths", sorted(newton_joints), sorted(path_by_joint_id.values()))
    child_poses, axes = reference.joint_X_c.numpy(), reference.joint_axis.numpy()
    limits_lo, limits_hi = reference.joint_limit_lower.numpy(), reference.joint_limit_upper.numpy()
    armature = reference.joint_armature.numpy()
    passive_damping = reference.joint_damping.numpy()
    for path, idx in joint_ids.items():
        if path not in newton_joints or idx < 0:
            continue
        ref = newton_joints[path]
        dof = dof_starts[ref]
        equal("Joint type", int(joint_types[joint_kind[ref]]), int(model.jnt_type[idx]), path)
        quat = child_poses[ref, [6, 3, 4, 5]].astype(float)
        close("Joint axis", rotation(quat) @ axes[dof], model.jnt_axis[idx], path)
        ref_limits = [float(limits_lo[dof]) if limits_lo[dof] > -newton.MAXVAL else None,
                      float(limits_hi[dof]) if limits_hi[dof] < newton.MAXVAL else None]
        limits = mujoco_joint_limits(model, idx)
        if limits is None:
            limits = (None, None)
        for expected, actual in zip(ref_limits, limits, strict=True):
            if expected is None or actual is None:
                equal("Joint limit", expected, None if actual is None else float(actual), path)
            else:
                close("Joint limit", expected, actual, path)
        address = model.jnt_dofadr[idx]
        close("Joint armature", armature[dof], model.dof_armature[address], path)
        close("Joint passive damping", passive_damping[dof], model.dof_damping[address], path)
        actuator = actuator_by_joint.get(idx)
        kp = model.actuator_gainprm[actuator, 0] if actuator is not None else 0.
        kv = -model.actuator_biasprm[actuator, 2] if actuator is not None else 0.
        close("Joint stiffness", ke[dof], kp, path)
        close("Joint damping", kd[dof], kv, path)

    geom_ids = ids(derived.collider_names_by_path, mujoco.mjtObj.mjOBJ_GEOM)
    flags = reference.shape_flags.numpy()
    shape_to_body = reference.shape_body.numpy()
    newton_colliders = {path: index for index, path in enumerate(reference.shape_label)
                        if flags[index] & int(newton.ShapeFlags.COLLIDE_SHAPES)
                        and path != "Validation/Ground"}
    equal("Collider Prim Paths", sorted(newton_colliders),
          sorted(path for path, idx in geom_ids.items() if idx >= 0))
    friction = np.column_stack((reference.shape_material_mu.numpy(),
                                reference.shape_material_mu_torsional.numpy(),
                                reference.shape_material_mu_rolling.numpy()))
    newton_body_paths = {-1: "/", **dict(enumerate(reference.body_label))}
    body_paths = {0: "/", **{idx: path for path, idx in body_ids.items() if idx > 0}}
    local_shapes = reference.shape_transform.numpy()
    for path, idx in geom_ids.items():
        if path in newton_colliders and idx >= 0:
            ref = newton_colliders[path]
            equal("Collider body", newton_body_paths[shape_to_body[ref]],
                  body_paths.get(model.geom_bodyid[idx]), path)
            close("Collider friction", friction[ref], model.geom_friction[idx], path)
            local = local_shapes[ref]
            shape_position = local[:3]
            shape_rotation = rotation(local[[6, 3, 4, 5]])
            body = shape_to_body[ref]
            if body >= 0:
                pose = newton_body_poses[body]
                body_rotation = newton_body_rotations[body]
                shape_position = pose[:3] + body_rotation @ shape_position
                shape_rotation = body_rotation @ shape_rotation
            geom_position = data.geom_xpos[idx]
            geom_rotation = data.geom_xmat[idx].reshape(3, 3)
            geom_type = model.geom_type[idx]
            if geom_type == mujoco.mjtGeom.mjGEOM_MESH:
                mesh = model.geom_dataid[idx]
                # MuJoCo compiles the mesh in its principal-axis frame and compensates in the geom.
                geom_rotation = geom_rotation @ rotation(model.mesh_quat[mesh]).T
                geom_position = geom_position - geom_rotation @ model.mesh_pos[mesh]
            close("Collider world position", shape_position - origin, geom_position, path, POSITION_ATOL, 0.)
            expected_axes = mujoco_collision_axes(geom_type, shape_rotation)
            if expected_axes is not None:
                actual_axes = mujoco_collision_axes(geom_type, geom_rotation)
                close("Collider world orientation", expected_axes, actual_axes, path, ORIENTATION_ATOL, 0.)

    newton_paths = {index: path for path, index in newton_colliders.items()}
    geom_paths = {index: path for path, index in geom_ids.items() if index >= 0}
    if mujoco_solver is None:
        # Newton's finalized candidates already account for group, world and explicit shape filtering.
        eligible = reference.shape_contact_pairs.numpy()
        source_paths = newton_paths
    else:
        # The live solver applies additional filtering, such as fixed bodies welded to the world.
        eligible = mujoco_contact_pairs(mujoco_solver.mj_model)
        source_paths = {
            geom: newton_paths[shape]
            for geom, shape in enumerate(mujoco_solver.mjc_geom_to_newton_shape.numpy()[0])
            if shape in newton_paths
        }
    expected_pairs = _named_pairs(((a, b) for a, b in eligible if a in source_paths and b in source_paths),
                                 source_paths)
    actual_pairs = _named_pairs(mujoco_contact_pairs(model), geom_paths)
    if expected_pairs != actual_pairs:
        missing, unexpected = expected_pairs - actual_pairs, actual_pairs - expected_pairs
        failures.append(check("P7", "fail", "Effective collision pairs differ between Newton and MuJoCo",
                              measurements={
                                  "missingGeometryPairCount": len(missing),
                                  "unexpectedGeometryPairCount": len(unexpected),
                                  "missingGeometryPairs": [list(pair) for pair in sorted(missing)[:20]],
                                  "unexpectedGeometryPairs": [list(pair) for pair in sorted(unexpected)[:20]],
                              }))

    if failures:
        return failures
    allowed_body_pairs = {
        tuple(sorted((shape_to_body[newton_colliders[a]], shape_to_body[newton_colliders[b]])))
        for a, b in expected_pairs
    }
    collider_bodies = {shape_to_body[index] for index in newton_paths}
    body_count = len(collider_bodies)
    return [check("P7", "pass", "Newton and compiled MuJoCo parameters agree", measurements={
        "rigidBodyCount": reference.body_count, "massKg": float(mass.sum()),
        "controlledJointCount": len(expected_order),
        "excludedBodyPairCount": body_count * (body_count - 1) // 2 - len(allowed_body_pairs),
        "collidableGeometryPairCount": len(expected_pairs),
        "collidableBodyPairCount": len(allowed_body_pairs),
        "colliderCount": len(newton_colliders),
    })]


def consistency_checks(payload, model, tools, *, newton_state=None, mujoco_solver=None):
    from importlib.metadata import PackageNotFoundError, version

    from pxr import UsdPhysics

    from apps.node.usd_mjcf import UnsupportedUsdPhysics, derive_usd_mjcf
    from packages.library.structural import USDGraph, training_scope
    from packages.sim_runtime.library_scene import asset_floor_offset

    scope = training_scope(payload)
    if scope is None or not model.body_count:
        return []
    graph = USDGraph(payload["package"], payload["assets"], payload.get("expectedAssets", []))
    stage = graph.compose(payload["metadata"]["stage"])
    if stage is None or graph.errors:
        return [check("P7", "fail", "Could not compose the USD stage for model consistency")]
    root = stage.GetPrimAtPath(scope)
    if not root:
        return [check("P7", "fail", "USD training root is missing", entity=scope)]
    if payload["kind"] == "scene" and not any(
        prim.IsA(UsdPhysics.Joint) or prim.HasAPI(UsdPhysics.ArticulationRootAPI) for prim in stage.Traverse()
    ):
        return []
    try:
        tools["mujoco"] = version("mujoco")
    except PackageNotFoundError:
        return [check("P7", "warn", "Model consistency was not measured because MuJoCo is not installed")]
    try:
        derived = derive_usd_mjcf(stage, prim_path=scope, allow_multiple_roots=payload["kind"] == "scene")
        return compare_models(
            model, derived, newton_state=newton_state, mujoco_solver=mujoco_solver,
            newton_origin_z=asset_floor_offset(stage) if payload["kind"] == "asset" else 0.,
        )
    except UnsupportedUsdPhysics as exc:
        return [check("P7", "fail", str(exc), entity=exc.prim_path)]
    except (ValueError, RuntimeError) as exc:
        return [check("P7", "fail", f"Could not compile or compare the derived MuJoCo model ({exc})")]
