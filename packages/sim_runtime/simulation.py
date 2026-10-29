"""A reusable real-Newton step boundary, independent of HTTP and IPC."""

import os
from time import perf_counter

import newton
import numpy as np
import warp as wp
from newton.solvers import SolverFeatherstone, SolverMuJoCo, SolverXPBD

from .scenes import get_scene_config
from .sites import add_joint_anchor_sites


class NewtonSimulation:
    def __init__(
        self, scene_id="falling-boxes", *, device="cpu", physics_dt=1 / 240, solver=None, config=None
    ):
        if physics_dt <= 0:
            raise ValueError("physics_dt must be positive")
        self.scene_id = scene_id
        self.physics_dt = physics_dt
        self.config = config if config is not None else get_scene_config(scene_id)
        self.solver_kind = solver or self.config.solver
        if self.solver_kind not in ("xpbd", "mujoco", "featherstone"):
            raise ValueError(f"Unsupported scene solver: {self.solver_kind}")
        with wp.ScopedDevice(device):
            self.builder = self.config.builder()
        self.missing_labels = set(getattr(self.builder, "spatial_missing_labels", ()))
        self.anchor_sites = add_joint_anchor_sites(self.builder)
        self.model = self.builder.finalize(device=device)
        self.state = self.model.state()
        self.next_state = self.model.state()
        self.control = self.model.control()
        self.native_cpu_sleeping = False
        with wp.ScopedDevice(self.model.device):
            if self.solver_kind == "mujoco":
                self.solver = SolverMuJoCo(
                    self.model,
                    use_mujoco_cpu=self.model.device.is_cpu,
                    use_mujoco_contacts=True,
                    iterations=20,
                    ls_iterations=10,
                    njmax=2048,
                    nconmax=2048,
                )
                if self.model.device.is_cpu:
                    from .mujoco_bvh import normalize_body_bvh

                    normalize_body_bvh(self.solver.mj_model)
                    if os.getenv("SPATIAL_NEWTON_CPU_SLEEPING") == "1":
                        import mujoco

                        # Native CPU support; Newton's enable_sleeping=True
                        # option is GPU-only. Preserve compiled AUTO/NEVER policy.
                        self.solver.mj_model.opt.enableflags |= int(mujoco.mjtEnableBit.mjENBL_SLEEP)
                        self.native_cpu_sleeping = True
                self.collision_pipeline, self.contacts = None, None
            else:
                self.solver = (
                    SolverXPBD(self.model, iterations=8)
                    if self.solver_kind == "xpbd"
                    else SolverFeatherstone(self.model)
                )
                self.collision_pipeline = newton.CollisionPipeline(self.model)
                self.contacts = self.collision_pipeline.contacts()
        self.driver = self.config.driver_factory(self.model) if self.config.driver_factory else None
        self.sim_time = 0.0
        self.epoch = 0
        self.paused = False
        self.solver_ms = 0.0
        self._control_callback, self._reset_callback = None, None
        self._control_dt, self._last_control_time, self._next_control_time = 1 / 60, 0.0, 1 / 60
        self.reset(increment_epoch=False)

    def install_program_loop(self, control_hz, tick, reset):
        self._control_dt = 1 / control_hz
        self._last_control_time = self.sim_time
        self._next_control_time = self.sim_time + self._control_dt
        self._control_callback, self._reset_callback = tick, reset

    def reset(self, *, increment_epoch=True):
        self.state = self.model.state()
        self.next_state = self.model.state()
        self.solver.reset(self.state)
        self.control.clear(self.model)
        newton.eval_fk(self.model, self.state.joint_q, self.state.joint_qd, self.state)
        newton.eval_fk(self.model, self.next_state.joint_q, self.next_state.joint_qd, self.next_state)
        self.sim_time = 0.0
        if increment_epoch:
            self.epoch += 1
        self._last_control_time, self._next_control_time = 0.0, self._control_dt
        if self._reset_callback:
            self._reset_callback()

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
                if self.driver:
                    self.driver.apply(self.control, self.sim_time)
                if self.collision_pipeline:
                    self.collision_pipeline.collide(self.state, self.contacts)
                self.solver.step(self.state, self.next_state, self.control, self.contacts, self.physics_dt)
                self.state, self.next_state = self.next_state, self.state
                self.sim_time += self.physics_dt
                if self._control_callback and self.sim_time + 1e-10 >= self._next_control_time:
                    self._control_callback(self.sim_time, self.sim_time - self._last_control_time)
                    self._last_control_time = self.sim_time
                    self._next_control_time += self._control_dt
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
