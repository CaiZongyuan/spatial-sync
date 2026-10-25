"""Fixed robot facts and action lifecycle around an approved Runner-loaded delegate."""

import multiprocessing as mp
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import JsonValue

from packages.info.program import TERMINAL, Reject
from packages.programs.metadata import ProgramIntent
from packages.programs.subjects import fingerprint

from .physical import RobotArmPhysicsConfig, RobotArmPhysicsFacts

_prepared = ContextVar("runner_script_programs", default=None)
PHYSICAL_FACTS = {"jointPositions", "gripperWidth", "tcpPose", "holding", "atLocation", "zone"}


@contextmanager
def prepared_script_programs(programs):
    if mp.parent_process() is None or not mp.current_process().name.startswith("newton-"):
        raise ValueError("Script execution requires the owned Newton Runner")
    token = _prepared.set(programs)
    try:
        yield
    finally:
        _prepared.reset(token)


@dataclass(frozen=True)
class _DelegateContext:
    original: object
    config: object

    def __getattr__(self, name):
        return getattr(self.original, name)

    @staticmethod
    def _check(fields):
        if PHYSICAL_FACTS.intersection(fields):
            raise ValueError("Physical feedback and zone facts belong to the fixed PhysicsPort wrapper")

    def set(self, **fields):
        self._check(fields)
        return self.original.set(**fields)

    def set_observed(self, observed_time, sample_time_ms, **fields):
        self._check(fields)
        return self.original.set_observed(observed_time, sample_time_ms, **fields)


class ScriptProgram(RobotArmPhysicsFacts):
    id, type, tick_priority = "robot_arm.script", "robot-arm/v1", 10
    evaluation_implementation_role = "fixed-runtime"

    class Config(RobotArmPhysicsConfig):
        programArtifact: ProgramIntent
        parameters: dict[str, JsonValue]

    def __init__(self):
        self.prepared = _prepared.get()
        if self.prepared is None:
            raise ValueError("Script initialization requires an approved owned Runner context")

    def init(self, ctx):
        if ctx.physics is None:
            raise ValueError("robot_arm.script requires the Newton PhysicsPort")
        prepared = self.prepared[ctx.key]
        prepared.initialized = False
        self.record, self.delegate = prepared.record, prepared.delegate
        if (
            self.record["id"] != ctx.config.programArtifact.id
            or self.record["contentHash"] != ctx.config.programArtifact.contentHash
            or fingerprint(ctx.config.parameters) != fingerprint(self.record["metadata"]["config"])
        ):
            raise ValueError("Prepared script material/config differs from the frozen robot binding")
        self.context = ctx
        self.delegate_context = _DelegateContext(ctx, prepared.config)
        self.joints = {
            key: ctx.physics.bind_joint(key) for key in ctx.config.jointKeys + ctx.config.fingerKeys
        }
        self.tubes = {key: ctx.physics.bind_body(key) for key in ctx.config.tubes}
        self.tcp = ctx.physics.bind_site(ctx.config.tcpSite)
        self.locations = ctx.config.locations["locations"]
        self.action = self.record["metadata"]["implements"].rsplit(".", 1)[1]
        self.handle = None
        self.calls = dict.fromkeys(("init", "accept", "start", "tick", "cancel"), 0)
        self.errors, self.action_starts = 0, 0
        self._facts(ctx)
        ctx.set(phase="idle")
        self._callback("init", self.delegate_context)
        self._hold(ctx)  # Initialization retains the fixed Scene's initial actuator state.
        prepared.initialized = True

    def _hold(self, ctx):
        for joint in self.joints.values():
            ctx.physics.set_joint_target(joint, ctx.physics.joint_q(joint))

    def _callback(self, method, *args):
        self.calls[method] += 1
        try:
            return getattr(self.delegate, method)(*args)
        except Exception:
            self.errors += 1
            self._hold(self.context)
            if self.handle is not None:
                self.handle.fail("program_execution_failed", "Script callback failed")
                self.handle = None
            self.context.set(phase="fault")
            raise

    def accept(self, inv, ctx):
        if inv.action == "stop":
            return None
        if inv.action != self.action:
            return Reject(code="precondition_failed", message="Script does not implement this action")
        try:
            zone = self._motion_zone(inv.params)
            for parameter in ("location", "from", "to"):
                if parameter in inv.params:
                    failed = ctx.check(self.locations[inv.params[parameter]].get("requires", []))
                    if failed:
                        return Reject(
                            code="precondition_failed",
                            message="Task location is unavailable",
                            details={"failed": failed},
                        )
        except (KeyError, ValueError) as error:
            return Reject(code="precondition_failed", message=str(error))
        ctx.set(zone=zone)
        return self._callback("accept", inv, self.delegate_context)

    def _finish(self, ctx):
        if self.handle is not None and self.handle.invocation.status in TERMINAL:
            self._hold(ctx)
            ctx.set(phase="fault" if self.handle.invocation.status == "failed" else "idle")
            self.handle = None
            self._facts(ctx)

    def start(self, inv, ctx):
        if inv.action == "stop":
            if self.handle is not None:
                self.cancel(self.handle, ctx)
            self._hold(ctx)
            ctx.set(phase="idle")
            inv.succeed()
            return
        self.handle, self.started = inv, ctx.sim_time
        self.action_starts += 1
        ctx.set(phase="moving", zone=self._motion_zone(inv.params))
        self._callback("start", inv, self.delegate_context)
        self._finish(ctx)

    def tick(self, ctx, dt):
        self._finish(ctx)
        if self.handle is not None:
            if ctx.sim_time - self.started >= ctx.config.maxSeconds:
                self._hold(ctx)
                self.handle.fail("program_timeout", "Script action exceeded its finite deadline")
                self.handle = None
                ctx.set(phase="fault")
            else:
                self._callback("tick", self.delegate_context, dt)
                self._finish(ctx)
        if ctx.fact_due:
            self._facts(ctx)

    def cancel(self, inv, ctx):
        try:
            self._callback("cancel", inv, self.delegate_context)
        finally:
            self._hold(ctx)
            inv.cancelled()
            self.handle = None
            ctx.set(phase="fault" if inv.invocation.status == "failed" else "idle")

    def execution_evidence(self):
        return {
            "kind": "program",
            "program": self.record["id"],
            "programContentHash": self.record["contentHash"],
            "codeHash": self.record["codeHash"],
            "configHash": self.record["configHash"],
            "archiveSha256": self.record["archiveSha256"],
            "actionStarts": self.action_starts,
            "callbacks": dict(self.calls),
            "callbackErrors": self.errors,
        }
