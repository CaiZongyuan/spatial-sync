"""Compare the imported Newton model with the trainer's compiled MuJoCo model."""

from collections import Counter

from packages.library.physics_checks import check

REL_TOL = 1e-4
MASS_ATOL = 1e-6  # kg
INERTIA_ATOL = 1e-8  # kg m²
PARAMETER_ATOL = 1e-6  # Coordinates and drive/contact parameters in solver units


def compare_models(newton_model, derived):
    import mujoco
    import newton
    import numpy as np

    model = mujoco.MjModel.from_xml_string(derived.mjcf_xml)
    reference = newton_model
    failures = []

    def equal(field, expected, actual, path=None):
        if expected != actual:
            failures.append(check("P7", "fail", f"{field} differs between Newton and MuJoCo", entity=path,
                                  measurements={"newton": expected, "mujoco": actual}))

    def close(field, expected, actual, path=None, atol=PARAMETER_ATOL):
        left, right = np.asarray(expected, dtype=float), np.asarray(actual, dtype=float)
        if left.shape == right.shape and np.allclose(left, right, atol=atol, rtol=REL_TOL):
            return
        if np.isfinite(left).all() and np.isfinite(right).all():
            equal(field, left.tolist(), right.tolist(), path)
        else:
            failures.append(check("P7", "fail", f"{field} has non-finite values", entity=path))

    def ids(names, kind):
        return {path: mujoco.mj_name2id(model, kind, name) for path, name in names.items()}

    def rotation(quat):
        matrix = np.empty(9)
        mujoco.mju_quat2Mat(matrix, quat)
        return matrix.reshape(3, 3)

    body_ids = ids(derived.body_names_by_path, mujoco.mjtObj.mjOBJ_BODY)
    newton_bodies = dict(zip(reference.body_label, range(reference.body_count), strict=True))
    equal("Rigid-body count", reference.body_count, model.nbody - 1)
    equal("Rigid-body Prim Paths", sorted(newton_bodies),
          sorted(path for path, idx in body_ids.items() if idx > 0))
    mass = reference.body_mass.numpy()
    inertia = reference.body_inertia.numpy()
    close("Total mass", float(mass.sum()), float(model.body_mass.sum()), atol=MASS_ATOL)
    for path, idx in body_ids.items():
        if path not in newton_bodies or idx < 1:
            continue
        ref = newton_bodies[path]
        close("Body mass", mass[ref], model.body_mass[idx], path, MASS_ATOL)
        principal_axes = rotation(model.body_iquat[idx])
        tensor = (principal_axes * model.body_inertia[idx]) @ principal_axes.T
        close("Body inertia in the link frame", inertia[ref], tensor, path, INERTIA_ATOL)

    joint_ids = ids({joint.prim_path: joint.name for joint in derived.joints}, mujoco.mjtObj.mjOBJ_JOINT)
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
    actual_order = [path_by_joint_id.get(int(idx), f"joint index {idx}")
                    for idx in model.actuator_trnid[:, 0]]
    equal("Controlled joint order", expected_order, actual_order)
    equal("Scalar joint Prim Paths", sorted(newton_joints), sorted(path_by_joint_id.values()))
    child_poses, axes = reference.joint_X_c.numpy(), reference.joint_axis.numpy()
    limits_lo, limits_hi = reference.joint_limit_lower.numpy(), reference.joint_limit_upper.numpy()
    armature = reference.joint_armature.numpy()
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
        limits = model.jnt_range[idx] if model.jnt_limited[idx] else (None, None)
        for expected, actual in zip(ref_limits, limits, strict=True):
            if expected is None or actual is None:
                equal("Joint limit", expected, None if actual is None else float(actual), path)
            else:
                close("Joint limit", expected, actual, path)
        address = model.jnt_dofadr[idx]
        close("Joint armature", armature[dof], model.dof_armature[address], path)
        actuator = actuator_by_joint.get(idx)
        kp = model.actuator_gainprm[actuator, 0] if actuator is not None else 0.
        kv = -model.actuator_biasprm[actuator, 2] if actuator is not None else 0.
        close("Joint stiffness", ke[dof], kp, path)
        close("Joint damping", kd[dof], kv, path)

    geom_ids = ids(derived.collider_names_by_path, mujoco.mjtObj.mjOBJ_GEOM)
    flags = reference.shape_flags.numpy()
    shape_to_body = reference.shape_body.numpy()
    model_collides = (model.geom_contype != 0) | (model.geom_conaffinity != 0)
    newton_colliders = {path: index for index, path in enumerate(reference.shape_label)
                        if flags[index] & int(newton.ShapeFlags.COLLIDE_SHAPES)
                        and path != "Validation/Ground"}
    equal("Collider Prim Paths", sorted(newton_colliders),
          sorted(path for path, idx in geom_ids.items() if idx >= 0 and model_collides[idx]))
    equal("Collider count", len(newton_colliders), int(model_collides.sum()))
    friction = np.column_stack((reference.shape_material_mu.numpy(),
                                reference.shape_material_mu_torsional.numpy(),
                                reference.shape_material_mu_rolling.numpy()))
    newton_body_paths = {-1: "/", **dict(enumerate(reference.body_label))}
    body_paths = {0: "/", **{idx: path for path, idx in body_ids.items() if idx > 0}}
    for path, idx in geom_ids.items():
        if path in newton_colliders and idx >= 0:
            ref = newton_colliders[path]
            equal("Collider body", newton_body_paths[shape_to_body[ref]],
                  body_paths.get(model.geom_bodyid[idx]), path)
            close("Collider friction", friction[ref], model.geom_friction[idx], path)

    # Compare actual Newton shape filters with the compiled MJCF body exclusions.
    # Same-body pairs never collide, and the validation-only ground is outside the authored stage.
    selected = set(newton_colliders.values())
    per_body = Counter(int(shape_to_body[index]) for index in selected)
    filtered = Counter()
    for first, second in reference.shape_collision_filter_pairs_array():
        a, b = sorted((int(shape_to_body[first]), int(shape_to_body[second])))
        if a != b and first in selected and second in selected:
            filtered[a, b] += 1
    expected_pairs = []
    for (a, b), count in filtered.items():
        pair = sorted((newton_body_paths[a], newton_body_paths[b]))
        if count != per_body[a] * per_body[b]:
            failures.append(check("P7", "fail", f"Newton filters only some colliders of {pair}"))
        else:
            expected_pairs.append(pair)
    # MuJoCo stores the two body IDs packed into a 32-bit signature.
    actual_pairs = [sorted((body_paths.get(int(sig) >> 16, "?"), body_paths.get(int(sig) & 0xffff, "?")))
                    for sig in model.exclude_signature]
    equal("Collision exclusions", sorted(expected_pairs), sorted(actual_pairs))

    if failures:
        return failures
    return [check("P7", "pass", "Newton and compiled MuJoCo parameters agree", measurements={
        "rigidBodyCount": reference.body_count, "massKg": float(mass.sum()),
        "controlledJointCount": len(expected_order), "excludedBodyPairCount": len(expected_pairs),
        "colliderCount": len(newton_colliders),
    })]


def consistency_checks(payload, model, tools):
    from importlib.metadata import PackageNotFoundError, version

    from pxr import UsdPhysics

    from apps.node.usd_mjcf import UnsupportedUsdPhysics, derive_usd_mjcf
    from packages.library.structural import USDGraph, training_scope

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
        return compare_models(model, derived)
    except UnsupportedUsdPhysics as exc:
        return [check("P7", "fail", str(exc), entity=exc.prim_path)]
    except (ValueError, RuntimeError) as exc:
        return [check("P7", "fail", f"Could not compile or compare the derived MuJoCo model ({exc})")]
