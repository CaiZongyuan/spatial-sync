"""Own programs beside physics and keep reliable publication off the solver path."""

from collections import deque
from contextlib import nullcontext
from time import perf_counter

import numpy as np

from packages.info.program import HostCore

from .info_catalog import REGISTRY, load_info_catalog
from .physics_port import PhysicsPort


class NewtonInfoHost:
    def __init__(
        self,
        simulation,
        manifest,
        options,
        emit,
        *,
        scene_package=None,
        policy_subject=None,
        simulation_subject=None,
        deployment_permit=None,
        state_root=None,
    ):
        self.sim, self.emit = simulation, emit
        self.catalog = load_info_catalog(
            manifest,
            simulation.scene_id,
            scene_package=scene_package,
            policy_subject=policy_subject,
            simulation_subject=simulation_subject,
        )
        from packages.programs.simulation import select_subject

        subject = select_subject(simulation_subject=simulation_subject, policy_subject=policy_subject)
        scripts = {
            key: binding
            for key, binding in self.catalog.entities.items()
            if binding["program"] == "robot_arm.script"
        }
        self.script_loader = None
        prepared = {}
        if deployment_permit is not None:
            from packages.programs.deployment import verify_runtime_deployment

            if subject is not None:
                raise ValueError("An evaluation replacement cannot also carry a deployment permit")
            verify_runtime_deployment(scene_package, self.catalog, deployment_permit, state_root=state_root)
        if scripts:
            from packages.programs.loading import ProgramLoader

            if state_root is None or (subject is None and deployment_permit is None):
                raise ValueError("Every script binding requires exact approved Runner execution proof")
            self.script_loader = ProgramLoader(manifest.sessionId, state_root=state_root)
            try:
                if deployment_permit is not None:
                    for key in scripts:
                        prepared[key] = self.script_loader.load_deployment(
                            deployment_permit, scene=scene_package, catalog=self.catalog, entity_key=key
                        )
                else:
                    from packages.programs.script_subjects import check_program_material

                    if subject.schema_version != "lw.script-subject/1" or set(scripts) != {
                        subject.target.entityKey
                    }:
                        raise ValueError("Evaluation script bindings differ from the exact admitted target")
                    check_program_material(subject, self.script_loader.library)
                    prepared[subject.target.entityKey] = self.script_loader.load_evaluation(
                        subject.codeApproval
                    )
            except BaseException:
                self.script_loader.close()
                raise
        self.port = PhysicsPort(simulation, manifest) if self.catalog.entities else None
        try:
            scope = nullcontext()
            if prepared:
                from packages.programs.robot_arm.script import prepared_script_programs

                scope = prepared_script_programs(prepared)
            with scope:
                self.host = HostCore(
                    self.catalog,
                    REGISTRY,
                    physics=self.port,
                    source="sim",
                    fact_hz=options.factHz,
                    seed=options.seed,
                )
            if any(not program.initialized for program in prepared.values()):
                raise ValueError("Script initialization failed before Runner readiness")
        except BaseException:
            self.close()
            raise
        self.timings = deque(maxlen=6000)
        self.tick_ms = 0.0
        simulation.install_program_loop(options.controlHz, self.tick, self.reset)

    def close(self):
        if self.script_loader is not None:
            self.script_loader.close()

    def tick(self, sim_time, dt):
        started = perf_counter()
        if self.port:
            self.port.capture()
        events = self.host.tick(sim_time, dt)
        if self.port:
            self.port.commit()
        self.tick_ms = (perf_counter() - started) * 1000
        self.timings.append(self.tick_ms)
        self.emit(events)

    def flush(self):
        self.emit(self.host.drain_events())

    def _intake(self, callback, argument, *, admit=None):
        if self.port:
            self.port.capture()
        if admit is not None and not admit():
            raise TimeoutError("Runner intake lease expired before Host admission")
        try:
            result = callback(argument)
        finally:
            # accept/cancel can establish holding targets while PAUSED. These
            # must reach the solver before even the first resumed physics step.
            if self.port:
                self.port.commit()
        self.flush()
        return result

    def invoke(self, request, *, admit=None):
        result = self._intake(self.host.invoke, request, admit=admit)
        self.emit([result])
        return result

    def cancel(self, invocation_id, *, admit=None):
        return self._intake(self.host.cancel, invocation_id, admit=admit)

    def revoke_before_start(self, invocation_id, *, admit=None):
        return self._intake(self.host.revoke_before_start, invocation_id, admit=admit)

    def reset(self):
        self.timings.clear()
        self.tick_ms = 0.0
        if self.port:
            self.port.reset()
        self.emit(self.host.reset(self.sim.epoch, self.sim.sim_time))

    def snapshot(self):
        return self.host.snapshot()

    def program_execution(self, entity_key):
        return self.host.program_execution(entity_key)

    def preview(self, request):
        return self.host.preview(request)

    def stats(self):
        declared, active = self.port.welds.counts() if self.port else (0, 0)
        return {
            "tick_ms": self.tick_ms,
            "tickP95Ms": float(np.percentile(self.timings, 95)) if self.timings else 0.0,
            "controlTickCount": self.host.tick_index,
            "tickTimingSamples": len(self.timings),
            "declaredWeldCount": declared,
            "activeWeldCount": active,
        }
