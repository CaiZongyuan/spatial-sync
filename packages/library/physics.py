"""Independent Newton CPU worker. Importing this module does not import its SDK."""

from __future__ import annotations

import json
import math
import platform
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from packages.library.physics_checks import (
    check,
    finite_state,
    joint_limit,
    mass_density,
    penetration,
    settling,
    tcp_error,
)
from packages.protocol.math import compose_pose, normalize_entity_key


def skipped(reason, missing_tools=()):
    return {
        "physics": {"status": "skipped", "reason": reason, "missingTools": list(missing_tools)},
        "checks": [check(f"P{i}", "warn", reason) for i in range(1, 7)],
    }


def not_applicable(identifier, message):
    return check(identifier, "warn", message, notApplicable=True)


def model_state(model, state):
    samples = {}
    for owner, names in ((state, ("body_q", "body_qd", "joint_q", "joint_qd", "particle_q", "particle_qd")),
                         (model, ("body_mass", "body_inertia", "shape_transform", "shape_scale"))):
        for name in names:
            value = getattr(owner, name, None)
            if value is not None:
                samples[name] = value.numpy().reshape(-1).tolist()
    return samples


def simulation(payload):
    import newton
    import warp as wp
    from pxr import UsdGeom, UsdPhysics

    from packages.library.structural import USDGraph
    from packages.sim_runtime.scenes import SceneConfig
    from packages.sim_runtime.simulation import NewtonSimulation

    metadata = payload["metadata"]
    if metadata["stage"].startswith("python:"):
        return NewtonSimulation(metadata["stage"].removeprefix("python:"), device="cpu",
                                solver=(metadata.get("simulation") or {}).get("solver"))

    graph = USDGraph(payload["package"], payload["assets"], payload.get("expectedAssets", []))
    stage = graph.compose(metadata["stage"])
    if stage is None or graph.errors:
        raise ValueError("USD physics dependencies could not be composed")
    asset = payload["kind"] == "asset"
    root = "/Asset" if asset else "/World/Lab"
    if not stage.GetPrimAtPath(root):
        raise ValueError("USD physics root is missing")
    offset = 0.
    if asset:
        cache = UsdGeom.BBoxCache(0, ["default", "render", "proxy"])
        bottoms = [float(cache.ComputeWorldBound(prim).ComputeAlignedRange().GetMin()[2])
                   for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.CollisionAPI)
                   and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False]
        if bottoms:
            offset = -min(bottoms)
            if not math.isfinite(offset):
                raise ValueError("Asset collision bounds are not finite")

    def builder():
        result = newton.ModelBuilder(up_axis=newton.Axis.Z)
        if (metadata.get("simulation") or {}).get("solver") == "mujoco":
            from newton.solvers import SolverMuJoCo

            SolverMuJoCo.register_custom_attributes(result)
        paths = result.add_usd(stage, root_path=root, apply_up_axis_from_stage=False,
                               xform=wp.transform((0, 0, offset), wp.quat_identity()))
        for kind in ("body", "shape", "joint"):
            labels = getattr(result, f"{kind}_label")
            for path, index in paths[f"path_{kind}_map"].items():
                labels[index] = str(path)
        if not asset:
            add_taught_sites(result, stage, paths, payload)
        if asset:
            result.add_ground_plane(label="Validation/Ground")
        return result

    return NewtonSimulation("library-validation", device="cpu", config=SceneConfig(builder),
                            solver=(metadata.get("simulation") or {}).get("solver"))


def add_taught_sites(builder, stage, paths, payload):
    """A declared USD Site can be an ordinary Xform; measure its authored geometry."""
    import warp as wp
    from pxr import UsdGeom

    requested = set()
    for binding in payload.get("structure", {}).get("effectiveBindings", {}).get("newton", []):
        config = binding["config"]
        for name, role in payload["programs"][binding["program"]]["fields"].items():
            value = config.get(name)
            if role.get("spatialReference") == "site":
                requested.update([value] if isinstance(value, str) else value or [])
            if role.get("documentSchema") == "locations" and isinstance(value, dict):
                requested.update(location["site"] for location in value["locations"].values()
                                 if location.get("site"))
    shapes = {str(path) for path in paths["path_shape_map"]}
    bodies = {str(path): index for path, index in paths["path_body_map"].items()}
    cache = UsdGeom.XformCache()
    for key in sorted(requested - shapes):
        prim = stage.GetPrimAtPath(key)
        if not prim or not prim.IsA(UsdGeom.Xformable):
            continue
        world = cache.GetLocalToWorldTransform(prim)
        quaternion = world.ExtractRotationQuat()
        pose = wp.transform(tuple(world.ExtractTranslation()),
                            wp.quat(*quaternion.GetImaginary(), quaternion.GetReal()))
        owners = [path for path in bodies if key.startswith(path + "/")]
        body = bodies[max(owners, key=len)] if owners else -1
        if body >= 0:
            pose = wp.transform_multiply(wp.transform_inverse(builder.body_q[body]), pose)
        builder.add_site(body, xform=pose, label=key)


def contact_observations(sim):
    """Query the final state again; solver-input contacts belong to an earlier state."""
    import newton

    pipeline = sim.collision_pipeline or newton.CollisionPipeline(
        sim.model, include_static_kinematic_pairs=True
    )
    if not pipeline.include_static_kinematic_pairs:
        raise ValueError("Kinematic collision pairs were not queried")
    contacts = pipeline.contacts()
    pipeline.collide(sim.state, contacts)
    count = int(contacts.rigid_contact_count.numpy()[0])
    if count < 0 or count > contacts.rigid_contact_max:
        raise ValueError("Rigid contact buffer overflow")
    if int(contacts.contact_counters.numpy()[2]):
        raise ValueError("Contact reduction dropped observations")
    narrow = pipeline.narrow_phase
    buffers = [(pipeline, "broad_phase_pair_count", "broad_phase_shape_pairs")]
    buffers += [(narrow, name + "_count", name) for name in (
        "gjk_candidate_pairs", "split_gjk_work", "split_manifold_work", "shape_pairs_mesh",
        "triangle_pairs", "shape_pairs_mesh_plane", "shape_pairs_mesh_mesh", "shape_pairs_sdf_sdf",
    )]
    # Split work buffers use an explicit _items suffix in the pinned SDK.
    for owner, counter_name, buffer_name in buffers:
        counter = getattr(owner, counter_name, None)
        buffer = getattr(owner, buffer_name, None)
        if buffer is None:
            buffer = getattr(owner, buffer_name + "_items", None)
        if counter is not None and buffer is not None:
            observed = int(counter.numpy()[0])
            if observed < 0 or observed > buffer.shape[0]:
                raise ValueError("Collision candidate buffer overflow: " + counter_name)
    if narrow.split_gjk_mpr:
        counter = (pipeline.broad_phase_pair_count if narrow.sparse_gjk_pairs
                   else narrow.gjk_candidate_pairs_count)
        if int(counter.numpy()[0]) > narrow.split_query_results.shape[0]:
            raise ValueError("Collision split query buffer overflow")
    reducer = narrow.global_contact_reducer
    if reducer is not None and (
        int(reducer.ht_insert_failures.numpy()[0]) or int(reducer.buffer_overflows.numpy()[0])
    ):
        raise ValueError("Contact reduction overflow")
    model = sim.model
    poses = sim.state.body_q.numpy()
    bodies = model.shape_body.numpy()
    shape0, shape1 = contacts.rigid_contact_shape0.numpy(), contacts.rigid_contact_shape1.numpy()
    points0, points1 = contacts.rigid_contact_point0.numpy(), contacts.rigid_contact_point1.numpy()
    normals = contacts.rigid_contact_normal.numpy()
    margins0, margins1 = contacts.rigid_contact_margin0.numpy(), contacts.rigid_contact_margin1.numpy()

    def world_point(shape, point):
        if not 0 <= shape < model.shape_count:
            raise ValueError("Contact shape index is invalid")
        body = int(bodies[shape])
        if body == -1:
            return list(map(float, point))
        if not 0 <= body < len(poses):
            raise ValueError("Contact body index is invalid")
        return compose_pose({"p": poses[body, :3], "q": poses[body, 3:]},
                            {"p": point, "q": [0, 0, 0, 1]})["p"]

    rows = []
    for index in range(count):
        first, second = int(shape0[index]), int(shape1[index])
        p0, p1 = world_point(first, points0[index]), world_point(second, points1[index])
        normal = list(map(float, normals[index]))
        if not all(math.isfinite(x) for x in [*p0, *p1, *normal, margins0[index], margins1[index]]):
            raise ValueError("Contact geometry is not finite")
        if abs(math.hypot(*normal) - 1) > 1e-4:
            raise ValueError("Contact normal is not a unit vector")
        distance = sum(n * (b - a) for n, a, b in zip(normal, p0, p1, strict=True))
        distance -= float(margins0[index] + margins1[index])
        rows.append((normalize_entity_key(model.shape_label[first]),
                     normalize_entity_key(model.shape_label[second]), float(distance)))
    return rows


def density_checks(sim, metadata):
    import newton
    from newton.geometry import compute_inertia_shape

    model = sim.model
    bodies, flags = model.shape_body.numpy(), model.shape_flags.numpy()
    types, scales = model.shape_type.numpy(), model.shape_scale.numpy()
    masses = model.body_mass.numpy()
    volumes = {}
    for index in range(model.shape_count):
        body = int(bodies[index])
        if body < 0 or not int(flags[index]) & int(newton.ShapeFlags.COLLIDE_SHAPES):
            continue
        # Unit-density mass is the actual imported collider's solid volume in m³.
        volume, _, _ = compute_inertia_shape(types[index], scales[index], model.shape_source[index],
                                              density=1., is_solid=True)
        volumes[body] = volumes.get(body, 0.) + float(volume)
    rows = [mass_density(normalize_entity_key(model.body_label[index]), float(mass), volumes.get(index, 0.),
                         estimated=(metadata.get("physics") or {}).get("massSource") == "estimated")
            for index, mass in enumerate(masses) if mass > 0 or index in volumes]
    if not rows:
        rows = [not_applicable("P4", "Dressing has no rigid-body mass") if metadata.get("dressing")
                else check("P4", "fail", "Asset has no positive rigid-body mass and collision volume")]
    return rows


def initial_joint_checks(sim):
    import newton

    model = sim.model
    positions = sim.state.joint_q.numpy()
    qstarts, dstarts = model.joint_q_start.numpy(), model.joint_qd_start.numpy()
    lower, upper = model.joint_limit_lower.numpy(), model.joint_limit_upper.numpy()
    dimensions, kinds = model.joint_dof_dim.numpy(), model.joint_type.numpy()
    rows = []
    for joint, kind in enumerate(kinds):
        if kind in (newton.JointType.FREE, newton.JointType.FIXED):
            continue
        entity = normalize_entity_key(model.joint_label[joint])
        start, end = int(dstarts[joint]), int(dstarts[joint + 1])
        if int(qstarts[joint + 1] - qstarts[joint]) != end - start:
            bounded = any(lower[dof] > -newton.MAXVAL or upper[dof] < newton.MAXVAL
                          for dof in range(start, end))
            row = (check("P5", "warn", "Non-scalar joint position limits were not measured", entity=entity)
                   if bounded else not_applicable("P5", "Non-scalar joint has no position limits"))
            rows.append(row | {"entityKey": entity})
            continue
        for coordinate, dof in enumerate(range(start, end)):
            lo = None if lower[dof] <= -newton.MAXVAL else float(lower[dof])
            hi = None if upper[dof] >= newton.MAXVAL else float(upper[dof])
            row = joint_limit(entity, float(positions[int(qstarts[joint]) + coordinate]), lo, hi,
                              unit="m" if coordinate < dimensions[joint, 0] else "rad")
            row["coordinate"] = coordinate
            rows.append(row)
    return rows or [not_applicable("P5", "Scene has no bounded articulated joints")]


def taught_checks(sim, payload):
    bindings = payload["structure"].get("effectiveBindings", {}).get("newton", [])
    configured = [(binding, payload["programs"][binding["program"]]["fields"]) for binding in bindings]
    configured = [(binding, fields) for binding, fields in configured
                  if any(role.get("posture") for role in fields.values())]
    if not configured:
        return [not_applicable("P6", "Scene has no configured taught arm locations")]

    import newton

    model = sim.model
    sites, joints = {}, {}
    for index, flags in enumerate(model.shape_flags.numpy()):
        if int(flags) & int(newton.ShapeFlags.SITE):
            sites.setdefault(normalize_entity_key(model.shape_label[index]), []).append(index)
    for index, label in enumerate(model.joint_label):
        joints.setdefault(normalize_entity_key(label), []).append(index)
    starts, dstarts = model.joint_q_start.numpy(), model.joint_qd_start.numpy()
    lower, upper = model.joint_limit_lower.numpy(), model.joint_limit_upper.numpy()
    parents, transforms = model.shape_body.numpy(), model.shape_transform.numpy()

    def site_world(key, state):
        matches = sites[key]
        if len(matches) != 1:
            raise ValueError("Configured Site identity is ambiguous in the model")
        index = matches[0]
        local = {"p": transforms[index, :3], "q": transforms[index, 3:]}
        parent = int(parents[index])
        if parent == -1:
            return local["p"]
        pose = state.body_q.numpy()[parent]
        return compose_pose({"p": pose[:3], "q": pose[3:]}, local)["p"]

    rows = []
    for binding, fields in configured:
        entity = binding["key"]
        postures = [name for name, info in fields.items() if info.get("posture")]
        documents = [name for name, info in fields.items() if info.get("documentSchema") == "locations"]
        endpoints = [name for name, info in fields.items() if info.get("spatialReference") == "site"]
        if len(postures) != 1 or len(documents) != 1 or len(endpoints) != 1:
            rows.append(check("P6", "fail", "Taught Config roles are ambiguous", entity=entity))
            continue
        config = binding["config"]
        for name, location in config[documents[0]]["locations"].items():
            try:
                values, keys = location["grasp"], config[postures[0]]
                if len(keys) != len(values):
                    raise ValueError("Grasp joint count differs from Config")
                state = sim.model.state()
                q = state.joint_q.numpy().copy()
                for key, value in zip(keys, values, strict=True):
                    matches = joints[key]
                    if len(matches) != 1:
                        raise ValueError("Configured joint identity is ambiguous in the model")
                    joint = matches[0]
                    if starts[joint + 1] - starts[joint] != 1 or dstarts[joint + 1] - dstarts[joint] != 1:
                        raise ValueError("Grasp requires scalar configured joints")
                    dof = int(dstarts[joint])
                    if not lower[dof] <= value <= upper[dof]:
                        raise ValueError("Grasp is outside actual model joint limits")
                    q[int(starts[joint])] = value
                state.joint_q.assign(q)
                newton.eval_fk(sim.model, state.joint_q, state.joint_qd, state)
                rows.append(tcp_error(entity, name, list(map(float, site_world(config[endpoints[0]], state))),
                                      list(map(float, site_world(location["site"], state)))))
            except (ValueError, KeyError, TypeError):
                rows.append(check("P6", "fail", "Taught grasp has no valid Config/model TCP or target Site",
                                  entity=entity, location=name))
    return rows or [not_applicable("P6", "Scene has no configured taught arm locations")]


def validate(payload):
    if any(row["status"] == "fail" for row in payload.get("structure", {}).get("checks", [])):
        return skipped("Physics was not run because structural validation failed")
    if payload["kind"] == "scene" and "newton" not in payload["metadata"]["sources"]:
        return skipped("This scene has no Newton physics source")
    tools, missing = {}, []
    for name in ("newton", "warp-lang", "usd-core"):
        try:
            tools[name] = version(name)
        except PackageNotFoundError:
            missing.append(name)
    if missing:
        return skipped("Physics tools are not installed: " + ", ".join(missing), missing)
    # Import failures with installed distributions are worker failures, never missing-tool skips.
    import newton
    import warp as wp

    cache = payload.get("kernelCache")
    if cache:
        wp.config.kernel_cache_dir = cache
    # Geometry helpers can allocate temporary arrays without a device argument.
    # This process owns its default device as well as each explicit simulation model.
    wp.set_device("cpu")
    tools["python"] = platform.python_version()
    result = {"physics": {"status": "completed", "device": "cpu", "ranAt": datetime.now(UTC).isoformat(),
                          "toolVersions": tools}, "checks": []}
    rows = result["checks"]
    try:
        sim = simulation(payload)
    except Exception as exc:
        rows.append(check("P1", "fail", f"Newton model could not load ({type(exc).__name__})"))
        rows.extend(check(f"P{i}", "warn", "Not measured because model loading failed") for i in range(2, 7))
        return result
    with sim:
        if not sim.model.device.is_cpu:
            raise RuntimeError("Physics validation requires the actual CPU device")
        if sim.solver_kind == "mujoco":
            tools["mujoco"] = version("mujoco")
        rows.append(finite_state(model_state(sim.model, sim.state)))
        if rows[-1]["status"] == "fail":
            rows.extend(check(f"P{i}", "warn", "Not measured because initial model state is invalid")
                        for i in range(2, 7))
            return result
        if payload["kind"] == "scene":
            rows.extend(initial_joint_checks(sim))
            rows.extend(taught_checks(sim, payload))
            rows.append(not_applicable("P4", "Mass/density check applies to individual asset packages"))
        else:
            rows.extend(density_checks(sim, payload["metadata"]))
            rows.extend([not_applicable("P5", "Initial scene joint check applies to scene packages"),
                         not_applicable("P6", "Taught positions belong to a scene")])
        free = [int(child) for kind, child in zip(sim.model.joint_type.numpy(), sim.model.joint_child.numpy())
                if kind == newton.JointType.FREE]
        initial = sim.state.body_q.numpy().copy()
        try:
            sim.step(480)  # The reference is the initial state; no warmup or body exclusion.
            if abs(sim.sim_time - 2.) > 1e-10:
                raise ValueError("Settling did not measure exactly two simulation seconds")
            final = sim.state.body_q.numpy()
            rows.extend(settling(normalize_entity_key(sim.model.body_label[index]),
                                 initial[index, :3].tolist(), final[index, :3].tolist()) for index in free)
            if not free:
                rows.append(not_applicable("P2", "Model has no FREE rigid bodies"))
            if finite_state(model_state(sim.model, sim.state))["status"] == "fail":
                rows.append(check("P2", "fail", "Simulation produced non-finite state within two seconds"))
        except Exception as exc:
            rows.append(check("P2", "fail", f"Two-second simulation did not complete ({type(exc).__name__})"))
            rows.append(check("P3", "warn", "Contact separation was not measured: simulation incomplete"))
            return result
        try:
            static = sum(int(body) < 0 and bool(int(flags) & int(newton.ShapeFlags.COLLIDE_SHAPES))
                         for body, flags in zip(sim.model.shape_body.numpy(), sim.model.shape_flags.numpy()))
            row = penetration(contact_observations(sim), world_static_shapes=static)
            row.setdefault("measurements", {})["simTime"] = sim.sim_time
            rows.append(row)
        except (ValueError, RuntimeError) as exc:
            rows.append(check("P3", "fail", f"Collision observations are incomplete ({exc})"))
        result["physics"]["simulationSeconds"] = sim.sim_time
        result["physics"]["freeBodyCount"] = len(free)
        return result


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = validate(payload)
    Path(sys.argv[2]).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
