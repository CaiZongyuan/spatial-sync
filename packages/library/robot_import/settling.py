"""Compare two seconds of gravity on the imported and derived CPU models."""

import math

from packages.library.physics_checks import check


def measure(payload, sim):
    import mujoco
    import newton
    import numpy as np
    import warp as wp

    from apps.node.usd_mjcf import derive_usd_mjcf
    from packages.library.physics import contact_observations
    from packages.library.structural import USDGraph
    from packages.sim_runtime.library_scene import asset_floor_offset

    graph = USDGraph(payload["package"], [])
    stage = graph.compose(payload["metadata"]["stage"])
    derived = derive_usd_mjcf(stage, prim_path="/Asset", include_ground_plane=True)
    spec = derived.to_mjspec()
    spec.body(derived.root_body).pos[2] += asset_floor_offset(stage)
    model = spec.compile()
    data = mujoco.MjData(model)
    steps = math.ceil(2 / sim.physics_dt - 1e-10)
    model.opt.timestep = sim.physics_dt
    model.opt.gravity[:] = (0., 0., -9.81)
    scalar = {joint.prim_path: joint for joint in derived.joints}
    targets = sim.control.joint_target_q.numpy()
    starts = sim.model.joint_target_q_start.numpy()
    for index, path in enumerate(sim.model.joint_label):
        if path in scalar:
            targets[int(starts[index])] = scalar[path].default_position
    sim.control.joint_target_q.assign(targets)
    actuator_ids = {int(row[0]): i for i, row in enumerate(model.actuator_trnid)}
    for joint in derived.joints:
        index = model.joint(joint.name).id
        if joint.initial_position is not None:
            data.qpos[model.jnt_qposadr[index]] = joint.initial_position
        if index in actuator_ids:
            data.ctrl[actuator_ids[index]] = joint.default_position

    def bounds(positions):
        violations = {}
        for joint in derived.joints:
            if joint.limited:
                position = positions[joint.prim_path]
                excess = max(joint.lower - position, position - joint.upper)
                if excess > 1e-5:
                    violations[joint.prim_path] = {"excess": excess,
                                                  "unit": "rad" if joint.kind == "hinge" else "m"}
        return violations

    def observations(height, vertical, depths, positions):
        if not np.isfinite([height, vertical, *depths, *positions.values()]).all():
            raise ValueError("Settling state is non-finite")
        return {
            "baseHeightM": float(height),
            "baseTiltDeg": math.degrees(math.acos(float(np.clip(vertical, -1., 1.)))),
            "maxSelfPenetrationM": max(0., *depths) if depths else 0.,
            "jointLimitViolations": bounds(positions),
        }

    sim.step(steps)
    mujoco.mj_step(model, data, nstep=steps)
    mujoco.mj_forward(model, data)
    if not np.isfinite(data.qpos).all() or any(
        data.warning[key].number for key in (mujoco.mjtWarning.mjWARN_BADQPOS,
                                             mujoco.mjtWarning.mjWARN_BADQVEL,
                                             mujoco.mjtWarning.mjWARN_BADQACC)
    ):
        raise ValueError("MuJoCo settling produced an invalid state")

    root_path = next(path for path, name in derived.body_names_by_path.items() if name == derived.root_body)
    newton_body = sim.state.body_q.numpy()[sim.model.body_label.index(root_path)]
    quat = newton_body[3:]
    vertical = float(wp.quat_rotate(wp.quat(*quat), wp.vec3(0., 0., 1.))[2])
    newton_q = sim.state.joint_q.numpy()
    q_addresses = sim.model.joint_q_start.numpy()
    shape_types = sim.model.shape_flags.numpy()
    body_ids = sim.model.shape_body.numpy()
    colliders = {path for path, owner, flags in zip(sim.model.shape_label, body_ids, shape_types, strict=True)
                 if owner >= 0 and int(flags) & int(newton.ShapeFlags.COLLIDE_SHAPES)}
    newton_positions = {
        path: float(newton_q[q_addresses[i]])
        for i, path in enumerate(sim.model.joint_label) if path in scalar
    }
    newton_metrics = observations(
        newton_body[2], vertical, [-depth for a, b, depth in contact_observations(sim)
                                  if a in colliders and b in colliders],
        newton_positions,
    )
    root = model.body(derived.root_body).id
    training_metrics = observations(
        data.xpos[root, 2], data.xmat[root, 8],
        [-float(contact.dist) for contact in data.contact
         if all(model.geom_bodyid[index] > 0 for index in contact.geom)],
        {joint.prim_path: float(data.qpos[model.joint(joint.name).qposadr[0]]) for joint in derived.joints},
    )
    status = "pass"
    if any(row["jointLimitViolations"] or row["maxSelfPenetrationM"] > .002
           for row in (newton_metrics, training_metrics)):
        status = "warn"
    return check("P8", status, "Two-second CPU gravity settling measured on both models", entity=root_path,
                 measurements={"durationSeconds": steps * sim.physics_dt, "physicsDt": sim.physics_dt,
                               "newton": newton_metrics, "mujoco": training_metrics})
