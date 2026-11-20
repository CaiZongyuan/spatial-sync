"""Map a sealed episode seed to a physical initial joint coordinate."""

BUCKETS = 2**16


def trial_offset(seed, amplitude):
    return amplitude * (2 * ((seed % BUCKETS + 0.5) / BUCKETS) - 1)


def apply_trial(simulation, trial, seed):
    import newton
    import warp as wp

    model = simulation.model
    key = trial["jointKey"]
    matching = [index for index, label in enumerate(model.joint_label) if "/" + label.strip("/") == key]
    if len(matching) != 1:
        raise ValueError("Evaluation trial joint does not resolve uniquely")
    joint, = matching
    starts = model.joint_q_start.numpy()
    kind = {"rad": newton.JointType.REVOLUTE, "m": newton.JointType.PRISMATIC}[trial["unit"]]
    if starts[joint + 1] - starts[joint] != 1 or model.joint_type.numpy()[joint] != kind:
        raise ValueError("Evaluation trial joint kind differs from the frozen unit")
    coordinates = model.joint_q.numpy().copy()
    index = starts[joint]
    coordinates[index] += trial_offset(seed, trial["amplitude"])
    degree = model.joint_qd_start.numpy()[joint]
    lower = model.joint_limit_lower.numpy()[degree]
    upper = model.joint_limit_upper.numpy()[degree]
    if not lower <= coordinates[index] <= upper:
        raise ValueError("Evaluation trial coordinate exceeds the frozen joint limits")
    wp.copy(model.joint_q, wp.array(coordinates, dtype=model.joint_q.dtype, device=model.device))
    simulation.reset(increment_epoch=False)
