"""A reusable real-Newton step boundary, independent of HTTP and IPC."""

from time import perf_counter

import newton
import numpy as np
import warp as wp
from newton.solvers import SolverXPBD

from .scenes import build_scene
from .sites import add_joint_anchor_sites


class NewtonSimulation:
    def __init__(self, scene_id="falling-boxes", *, device="cpu", physics_dt=1 / 240):
        if physics_dt <= 0:
            raise ValueError("physics_dt must be positive")
        self.scene_id = scene_id
        self.physics_dt = physics_dt
        self.builder = build_scene(scene_id)
        self.missing_labels = set(getattr(self.builder, "spatial_missing_labels", ()))
        self.anchor_sites = add_joint_anchor_sites(self.builder)
        self.model = self.builder.finalize(device=device)
        self.state = self.model.state()
        self.next_state = self.model.state()
        self.control = self.model.control()
        self.solver = SolverXPBD(self.model, iterations=8)
        self.collision_pipeline = newton.CollisionPipeline(self.model)
        self.contacts = self.collision_pipeline.contacts()
        self.sim_time = 0.0
        self.epoch = 0
        self.paused = False
        self.solver_ms = 0.0
        self.reset(increment_epoch=False)

    def reset(self, *, increment_epoch=True):
        self.state = self.model.state()
        self.next_state = self.model.state()
        newton.eval_fk(self.model, self.state.joint_q, self.state.joint_qd, self.state)
        newton.eval_fk(self.model, self.next_state.joint_q, self.next_state.joint_qd, self.next_state)
        self.sim_time = 0.0
        if increment_epoch:
            self.epoch += 1

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def step(self, count=1):
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("step count must be a positive integer")
        started = perf_counter()
        with wp.ScopedDevice(self.model.device):
            for _ in range(count):
                self.state.clear_forces()
                self.collision_pipeline.collide(self.state, self.contacts)
                self.solver.step(self.state, self.next_state, self.control, self.contacts, self.physics_dt)
                self.state, self.next_state = self.next_state, self.state
                self.sim_time += self.physics_dt
        self.solver_ms = (perf_counter() - started) * 1000

    def advance(self, wall_seconds):
        if not self.paused:
            count = int(wall_seconds / self.physics_dt)
            if count:
                self.step(count)

    def body_poses(self) -> np.ndarray:
        """One bulk transfer at a safe step boundary, used by baseline diagnostics."""
        return self.state.body_q.numpy().copy()

    def close(self):
        wp.synchronize_device(self.model.device)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
