"""Feed-forward CPU policy Program. Actuation and facts use only PhysicsPort."""

import hashlib
from copy import deepcopy

import numpy as np
from pydantic import Field

from packages.info.program import Reject
from packages.library.common import document
from packages.policies.inference import CpuPolicy
from packages.policies.library import read_json, runtime_library
from packages.policies.observations import ObservationSpec, coverage_failure
from packages.programs.policy_base import (
    build_policy_evidence,
    inspect_port_joints,
    step_policy_inference,
    validate_action_against_joints,
)

from .physical import RobotArmPhysicsConfig, RobotArmPhysicsFacts


class PolicyProgram(RobotArmPhysicsFacts):
    id, type, tick_priority = "robot_arm.policy", "robot-arm/v1", 10
    policy_program = True
    evaluation_implementation_role = "fixed-runtime"

    @classmethod
    def supports_type(cls, entity_type: str) -> bool:
        return cls.type is None or cls.type == entity_type

    class Config(RobotArmPhysicsConfig):
        policy: str = Field(pattern=r"^policy/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")

    @classmethod
    def policy_joints(cls, config: dict) -> dict[str, str]:
        return {key: "rad" for key in config["jointKeys"]} | {key: "m" for key in config["fingerKeys"]}

    @classmethod
    def bound_policy_for_action(cls, config: dict, action: str) -> str | None:
        return config.get("policy")

    @classmethod
    def bound_policies(cls, config: dict) -> set[str]:
        return {config["policy"]} if config.get("policy") else set()

    @classmethod
    def apply_policy_override(cls, base_config: dict, action: str, policy_id: str) -> dict:
        allowed_fields = set(cls.Config.model_fields)
        raw = {
            name: deepcopy(value)
            for name, value in base_config.items()
            if name in allowed_fields and name != "policy"
        }
        raw["policy"] = policy_id
        return cls.Config.model_validate(raw).model_dump(mode="json")

    def init(self, ctx):
        if ctx.physics is None:
            raise ValueError("robot_arm.policy requires the Newton PhysicsPort")
        self.record, package = runtime_library().resolve(ctx.config.policy)
        self.task = read_json(package / "task.json")
        self.spec = ObservationSpec.model_validate(document(package / "obs_spec.yaml"))
        if self.task["robot"] != ctx.key or self.task["implements"].split(".")[0] != self.type:
            raise ValueError("Policy Task does not identify this robot/interface")
        fallback_units = {key: "rad" for key in ctx.config.jointKeys} | {
            key: "m" for key in ctx.config.fingerKeys
        }
        self.joints = {key: ctx.physics.bind_joint(key) for key in fallback_units}
        joint_units, self.joint_limits = inspect_port_joints(
            ctx.physics,
            self.joints,
            fallback_units=fallback_units,
            prismatic_keys=ctx.config.fingerKeys,
        )
        validate_action_against_joints(self.spec.action, joint_units, self.joint_limits)
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
            self.previous = step_policy_inference(
                ctx.physics,
                self.joints,
                self.spec,
                self.model,
                self.previous,
                self.observation_digest,
                references=self.references,
                joint_limits=getattr(self, "joint_limits", None),
            )
            self.next_inference += interval
        except (ValueError, KeyError, RuntimeError) as exc:
            self._hold(ctx)
            inv.fail("policy_inference_failed", str(exc))
            self.handle = None
            ctx.set(phase="fault")

    def execution_evidence(self):
        return build_policy_evidence(self.record, self.model, self.action_starts, self.observation_digest)
