"""Feed-forward CPU policy Program. Actuation and facts use only PhysicsPort."""

import hashlib

import numpy as np
from pydantic import Field

from packages.info.program import Reject
from packages.library.common import document
from packages.policies.inference import CpuPolicy
from packages.policies.library import read_json, runtime_library
from packages.policies.observations import ObservationSpec, assemble, coverage_failure, joint_targets

from .physical import RobotArmPhysicsConfig, RobotArmPhysicsFacts


class PolicyProgram(RobotArmPhysicsFacts):
    id, type, tick_priority = "robot_arm.policy", "robot-arm/v1", 10
    evaluation_implementation_role = "fixed-runtime"

    class Config(RobotArmPhysicsConfig):
        policy: str = Field(pattern=r"^policy/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")

    def init(self, ctx):
        if ctx.physics is None:
            raise ValueError("robot_arm.policy requires the Newton PhysicsPort")
        self.record, package = runtime_library().resolve(ctx.config.policy)
        self.task = read_json(package / "task.json")
        self.spec = ObservationSpec.model_validate(document(package / "obs_spec.yaml"))
        if self.task["robot"] != ctx.key or self.task["implements"].split(".")[0] != self.type:
            raise ValueError("Policy Task does not identify this robot/interface")
        allowed = set(ctx.config.jointKeys + ctx.config.fingerKeys)
        if not set(self.spec.action.jointKeys) <= allowed:
            raise ValueError("Policy action targets must belong to the configured arm/fingers")
        self.joints = {key: ctx.physics.bind_joint(key) for key in allowed}
        self.tubes = {key: ctx.physics.bind_body(key) for key in ctx.config.tubes}
        self.tcp = ctx.physics.bind_site(ctx.config.tcpSite)
        self.locations = ctx.config.locations["locations"]
        export = read_json(package / "artifacts/export.json")
        self.model = CpuPolicy(
            package / self.record["onnx"],
            self.spec,
            root=package / "artifacts",
            external=export["externalData"],
        )
        self.handle, self.references = None, {}
        self.action_starts = 0
        self.observation_digest = hashlib.sha256(b"lw.physics-policy-observations/1")
        self.previous = np.zeros(len(self.spec.action.jointKeys))
        self._facts(ctx)
        ctx.set(phase="idle")

    def _references(self, inv, ctx):
        refs = {}
        for parameter in ("from", "to"):
            if parameter in inv.params:
                name = inv.params[parameter]
                if name not in self.locations:
                    raise ValueError("Task parameter does not resolve to a configured location")
                refs["$" + parameter] = self.locations[name]["site"]
        if "$from" in refs:
            objects = [key for key in self.tubes if ctx.read(key, "location") == refs["$from"]]
            if len(objects) != 1:
                raise ValueError("Task source must contain exactly one configured object")
            refs["$object"] = objects[0]
        return refs

    def accept(self, inv, ctx):
        if inv.action == "stop":
            return None
        failure = coverage_failure(self.task, inv.action, inv.params)
        if failure:
            return Reject(**failure)
        try:
            self._references(inv, ctx)
            zone = self._motion_zone(inv.params)
        except (ValueError, KeyError) as exc:
            return Reject(code="precondition_failed", message=str(exc))
        for parameter in ("from", "to"):
            if parameter in inv.params:
                failed = ctx.check(self.locations[inv.params[parameter]].get("requires", []))
                if failed:
                    return Reject(
                        code="precondition_failed",
                        message="Task location is unavailable",
                        details={"failed": failed},
                    )
        # Authoritative intake also protects the gap before queued work starts.
        ctx.set(zone=zone)

    def _hold(self, ctx):
        for key in self.spec.action.jointKeys:
            ctx.physics.set_joint_target(self.joints[key], ctx.physics.joint_q(self.joints[key]))

    def start(self, inv, ctx):
        if inv.action == "stop":
            self._hold(ctx)
            if self.handle:
                self.handle.cancelled()
                self.handle = None
            ctx.set(phase="idle")
            inv.succeed()
            return
        self.references = self._references(inv, ctx)
        self.action_starts += 1
        self.handle, self.started, self.next_inference = inv, ctx.sim_time, ctx.sim_time
        self.previous[:] = 0
        # The black box has no trusted pick/place stage; reserve before its first target.
        ctx.set(phase="moving", zone=self._motion_zone(inv.params))

    def cancel(self, inv, ctx):
        self._hold(ctx)
        inv.cancelled()
        self.handle = None
        ctx.set(phase="idle")

    def _state(self, port):
        state = {"joints": {}, "bodies": {}, "sites": {}}

        def key(value):
            return self.references[value] if value.startswith("$") else value

        def pose(group, value):
            name = key(value)
            p, q = port.body_pose(name) if group == "bodies" else port.site_pose(name)
            state[group][name] = {"p": p, "q": q}

        for item in self.spec.observations:
            if item.source.startswith("joint_"):
                for value in item.keys:
                    name = key(value)
                    state["joints"][name] = {
                        "q": float(port.joint_q(name)[0]),
                        "qd": float(port.joint_qd(name)[0]),
                    }
            elif item.source.startswith(("body_", "site_")):
                pose("bodies" if item.source.startswith("body_") else "sites", item.key)
            if item.frame != "world":
                pose("bodies", item.frame)
        return state

    def tick(self, ctx, dt):
        if ctx.fact_due:
            self._facts(ctx)
        inv = self.handle
        if inv is None:
            return
        if not ctx.check([self.task["success"]]):
            self._hold(ctx)
            inv.succeed()
            self.handle = None
            ctx.set(phase="idle")
            return
        if ctx.sim_time - self.started >= ctx.config.maxSeconds:
            self._hold(ctx)
            inv.fail("motion_timeout", "Policy did not satisfy the pinned Task condition")
            self.handle = None
            ctx.set(phase="idle")
            return
        interval = 1 / self.task["control"]["hz"]
        if ctx.sim_time + 1e-10 < self.next_inference:
            return
        try:
            if dt > interval + 1e-10:
                raise ValueError("Runner control frequency is below the Task policy frequency")
            observation = assemble(
                self.spec, self._state(ctx.physics), previous_action=self.previous, references=self.references
            )
            self.observation_digest.update(observation.tobytes())
            output = self.model.infer(observation)
            positions = [ctx.physics.joint_q(self.joints[key])[0] for key in self.spec.action.jointKeys]
            targets = joint_targets(self.spec, positions, output)
            for key, value in zip(self.spec.action.jointKeys, targets, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])
            # previous_action always means raw model output, before action scale/clipping.
            self.previous = output.copy()
            self.next_inference += interval
        except (ValueError, KeyError, RuntimeError) as exc:
            self._hold(ctx)
            inv.fail("policy_inference_failed", str(exc))
            self.handle = None
            ctx.set(phase="fault")

    def execution_evidence(self):
        """Read-only native receipt; all counts originate at the actual CPU runtime."""
        return {
            "kind": "policy",
            "policy": self.record["id"],
            "policyContentHash": self.record["contentHash"],
            "onnxSha256": self.record["onnxSha256"],
            "observationSha256": self.record["observationSha256"],
            "providers": self.model.session.get_providers(),
            "runtimeVersion": self.model.runtime_version,
            "actionStarts": self.action_starts,
            "inferenceCalls": self.model.inference_calls,
            "validOutputs": self.model.valid_outputs,
            "observationDigest": self.observation_digest.hexdigest(),
        }
