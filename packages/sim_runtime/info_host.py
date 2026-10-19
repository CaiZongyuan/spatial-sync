"""Own programs beside physics and keep reliable publication off the solver path."""

from collections import deque
from time import perf_counter

import numpy as np

from packages.info.program import HostCore

from .info_catalog import REGISTRY, load_info_catalog
from .physics_port import PhysicsPort


class NewtonInfoHost:
    def __init__(self, simulation, manifest, options, emit, *, scene_package=None):
        self.sim, self.emit = simulation, emit
        self.port = PhysicsPort(simulation, manifest) if simulation.scene_id == "robot-and-tubes" else None
        self.catalog = load_info_catalog(manifest, simulation.scene_id, scene_package=scene_package)
        self.host = HostCore(self.catalog, REGISTRY, physics=self.port, source="sim", fact_hz=options.factHz)
        self.timings = deque(maxlen=6000)
        self.tick_ms = 0.0
        simulation.install_program_loop(options.controlHz, self.tick, self.reset)

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

    def invoke(self, request):
        result = self.host.invoke(request)
        self.flush()
        self.emit([result])
        return result

    def cancel(self, invocation_id):
        result = self.host.cancel(invocation_id)
        self.flush()
        return result

    def revoke_before_start(self, invocation_id):
        result = self.host.revoke_before_start(invocation_id)
        self.flush()
        return result

    def reset(self):
        self.timings.clear()
        self.tick_ms = 0.0
        if self.port:
            self.port.reset()
        self.emit(self.host.reset(self.sim.epoch, self.sim.sim_time))

    def snapshot(self):
        return self.host.snapshot()

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
