"""Graph-safe observation of each real MJWarp input/output; never writes physics state."""

from functools import wraps

import warp as wp

from .validity import EpisodeValidity


@wp.kernel
def observe_raw(
    q: wp.array(dtype=wp.float32),
    v: wp.array(dtype=wp.float32),
    bodies: wp.array(dtype=wp.transform),
    body_v: wp.array(dtype=wp.spatial_vector),
    q_ids: wp.array2d(dtype=wp.int32),
    v_ids: wp.array2d(dtype=wp.int32),
    lower: wp.array2d(dtype=wp.float32),
    upper: wp.array2d(dtype=wp.float32),
    body_ids: wp.array2d(dtype=wp.int32),
    lifted: wp.array(dtype=wp.int32),
    output: int,
    reasons: wp.array(dtype=wp.int32),
    first: wp.array(dtype=wp.int32),
    samples: wp.array(dtype=wp.int32),
    outputs: wp.array(dtype=wp.int32),
    transition_outputs: wp.array(dtype=wp.int32),
    max_excess: wp.array(dtype=wp.float32),
    max_speed: wp.array(dtype=wp.float32),
):
    world = wp.tid()
    flags = wp.int32(0)
    excess = wp.float32(0.0)
    speed = wp.float32(0.0)
    for joint in range(9):
        position = q[q_ids[world, joint]]
        velocity = v[v_ids[world, joint]]
        if not wp.isfinite(position) or not wp.isfinite(velocity):
            flags = flags | 8
        else:
            excess = wp.max(excess, wp.max(lower[world, joint] - position, position - upper[world, joint]))
            speed = wp.max(speed, wp.abs(velocity))
    if excess > 0.0:
        flags = flags | 1
    if speed > 12.0:
        flags = flags | 2
    for role in range(2):
        body = body_ids[world, role]
        pose = bodies[body]
        body_velocity = body_v[body]
        for component in range(7):
            if not wp.isfinite(pose[component]):
                flags = flags | 8
        for component in range(6):
            if not wp.isfinite(body_velocity[component]):
                flags = flags | 8
    local = wp.transform_point(
        wp.transform_inverse(bodies[body_ids[world, 0]]),
        wp.transform_get_translation(bodies[body_ids[world, 1]]),
    )
    if local[2] < -0.01 or wp.abs(local[0]) > 0.62 or wp.abs(local[1]) > 0.50:
        flags = flags | 4
    if lifted[world] != 0 and local[2] < 0.004:
        flags = flags | 4
    if flags != 0 and reasons[world] == 0:
        first[world] = samples[world]
    reasons[world] = reasons[world] | flags
    max_excess[world] = wp.max(max_excess[world], excess)
    max_speed[world] = wp.max(max_speed[world], speed)
    samples[world] += 1
    outputs[world] += output
    transition_outputs[world] += output


class NativeEpisodeObserver:
    def __init__(self, model):
        self.device = model.device
        self.validity = EpisodeValidity(model.world_count, str(model.device))
        names = [f"fr3_joint{i}" for i in range(1, 8)] + ["fr3_finger_joint1", "fr3_finger_joint2"]
        joints = {
            (int(world), label.rsplit("/", 1)[-1]): i
            for i, (world, label) in enumerate(zip(model.joint_world.numpy(), model.joint_label, strict=True))
        }
        bodies = {
            (int(world), label.rsplit("/", 1)[-1]): i
            for i, (world, label) in enumerate(zip(model.body_world.numpy(), model.body_label, strict=True))
        }
        qs, vs = model.joint_q_start.numpy(), model.joint_qd_start.numpy()
        parents = model.joint_parent.numpy()
        low, high = model.joint_limit_lower.numpy(), model.joint_limit_upper.numpy()
        selected = [[joints[(world, name)] for name in names] for world in range(model.world_count)]
        q_ids = [[int(qs[joint]) for joint in row] for row in selected]
        v_ids = [[int(vs[joint]) for joint in row] for row in selected]
        self.maps = [
            wp.array(q_ids, dtype=wp.int32, device=self.device),
            wp.array(v_ids, dtype=wp.int32, device=self.device),
            wp.array([[float(low[i]) for i in row] for row in v_ids], dtype=wp.float32, device=self.device),
            wp.array([[float(high[i]) for i in row] for row in v_ids], dtype=wp.float32, device=self.device),
            wp.array(
                [
                    [int(parents[row[0]]), bodies[(world, "Vial")]]
                    for world, row in enumerate(selected)
                ],
                dtype=wp.int32,
                device=self.device,
            ),
        ]
        self.buffers = [
            wp.from_torch(getattr(self.validity, name))
            for name in (
                "reasons",
                "first_sample",
                "samples",
                "outputs",
                "transition_outputs",
                "max_excess",
                "max_speed",
            )
        ]
        self.lifted = wp.from_torch(self.validity.lifted)

    def observe(self, state, *, output):
        wp.launch(
            observe_raw,
            dim=len(self.validity.reasons),
            inputs=[
                state.joint_q,
                state.joint_qd,
                state.body_q,
                state.body_qd,
                *self.maps,
                self.lifted,
                int(output),
                *self.buffers,
            ],
            device=self.device,
        )


def install():
    """Process-local hook before environment construction, including CUDA graph capture."""
    from newton._src.solvers.mujoco.solver_mujoco import SolverMuJoCo

    if getattr(SolverMuJoCo, "_fr3_episode_observer_installed", False):
        return
    original_init, original_step = SolverMuJoCo.__init__, SolverMuJoCo.step

    @wraps(original_init)
    def initialize(self, model, *args, **kwargs):
        original_init(self, model, *args, **kwargs)
        self._fr3_episode_observer = NativeEpisodeObserver(model)

    @wraps(original_step)
    def step(self, state_in, state_out, control, contacts, dt):
        observer = getattr(self, "_fr3_episode_observer", None)
        if observer is not None:
            observer.observe(state_in, output=False)
        original_step(self, state_in, state_out, control, contacts, dt)
        if observer is not None:
            observer.observe(state_out, output=True)

    SolverMuJoCo.__init__, SolverMuJoCo.step = initialize, step
    SolverMuJoCo._fr3_episode_observer_installed = True
