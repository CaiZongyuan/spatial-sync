"""Reusable CPU ONNX multi-policy Device Program base for general embodied robots."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.info.program import Reject
from packages.library.common import document
from packages.policies.inference import CpuPolicy
from packages.policies.library import read_json, runtime_library
from packages.policies.observations import (
    ObservationSpec,
    assemble,
    command_envelope_failure,
    coverage_failure,
    joint_targets,
)

POLICY_REF = r"^policy/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$"
ActionName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9]*$")]
PolicyRef = Annotated[str, Field(pattern=POLICY_REF)]
SpatialPath = Annotated[str, Field(min_length=1)]


class PolicySlot:
    """Isolated runtime state and native CPU ONNX execution counters for one bound action."""

    def __init__(self, action: str, policy_id: str, *, entity_key: str, interface: str, allowed_joints: dict):
        self.action = action
        self.record, self.package = runtime_library().resolve(policy_id)
        self.task = read_json(self.package / "task.json")
        self.spec = ObservationSpec.model_validate(document(self.package / "obs_spec.yaml"))
        if self.task["robot"] != entity_key or self.task["implements"] != f"{interface}.{action}":
            raise ValueError("Policy Task does not identify this robot entity and bound action")
        if not set(self.spec.action.jointKeys) <= set(allowed_joints):
            raise ValueError("Policy action targets must belong to the configured robot joints")
        expected_units = [allowed_joints[key] for key in self.spec.action.jointKeys]
        if self.spec.action.units != expected_units:
            raise ValueError("Policy action units differ from the configured robot joint kinds")
        export = read_json(self.package / "artifacts/export.json")
        self.model = CpuPolicy(
            self.package / self.record["onnx"],
            self.spec,
            root=self.package / "artifacts",
            external=export["externalData"],
        )
        self.action_starts = 0
        self.observation_digest = hashlib.sha256(b"lw.physics-policy-observations/1")
        self.previous = np.zeros(len(self.spec.action.jointKeys), dtype=np.float64)

    def evidence(self) -> dict:
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


class MultiPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jointKeys: list[SpatialPath] = Field(min_length=1, json_schema_extra={"spatialReference": "joint"})
    prismaticJointKeys: list[SpatialPath] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        json_schema_extra={"spatialReference": "joint"},
    )
    baseBody: SpatialPath | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        json_schema_extra={"spatialReference": "body"},
    )
    policies: dict[ActionName, PolicyRef] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
    )
    policy: PolicyRef | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    maxSeconds: float = Field(default=20.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_joints_and_policies(self):
        if len(set(self.jointKeys)) != len(self.jointKeys):
            raise ValueError("jointKeys must be distinct")
        if len(set(self.prismaticJointKeys)) != len(self.prismaticJointKeys) or not (
            set(self.prismaticJointKeys) <= set(self.jointKeys)
        ):
            raise ValueError("prismaticJointKeys must be distinct members of jointKeys")
        if bool(self.policies) == (self.policy is not None):
            raise ValueError("Configure either policies (action -> policy) or a single policy")
        return self


def extract_observation_state(port, spec, references):
    """Gather only the Newton PhysicsPort facts required by an ObservationSpec."""
    state = {"joints": {}, "bodies": {}, "sites": {}}

    def key(value):
        return references[value] if value.startswith("$") else value

    def ensure_pose(group, value):
        name = key(value)
        p, q = port.body_pose(name) if group == "bodies" else port.site_pose(name)
        state[group].setdefault(name, {}).update({"p": p, "q": q})
        return name

    for item in spec.observations:
        source = item.source
        if source.startswith("joint_"):
            for value in item.keys:
                name = key(value)
                state["joints"][name] = {
                    "q": float(port.joint_q(name)[0]),
                    "qd": float(port.joint_qd(name)[0]),
                }
        elif source == "body_angular_velocity":
            name = ensure_pose("bodies", item.key)
            state["bodies"][name]["w"] = port.body_angular_velocity(name, frame="world")
        elif source == "projected_gravity" or source.startswith("body_"):
            ensure_pose("bodies", item.key)
        elif source.startswith("site_"):
            ensure_pose("sites", item.key)
        if item.frame not in ("world", "body") and source != "command":
            ensure_pose("bodies", item.frame)
    return state


class MultiPolicyProgram:
    """Subclassable multi-action CPU ONNX policy program over Newton PhysicsPort."""

    policy_program = True
    evaluation_implementation_role = "fixed-runtime"
    Config = MultiPolicyConfig

    stop_actions: tuple[str, ...] = ("stop",)
    state_property: str | None = "phase"
    idle_state: str = "idle"
    active_state: str = "active"
    fault_state: str = "fault"

    @classmethod
    def policy_joints(cls, config: dict) -> dict[str, str]:
        prismatic = set(config.get("prismaticJointKeys") or ())
        return {key: ("m" if key in prismatic else "rad") for key in config["jointKeys"]}

    @classmethod
    def bound_policy_for_action(cls, config: dict, action: str) -> str | None:
        policies = config.get("policies")
        if policies:
            return policies.get(action)
        return config.get("policy")

    @classmethod
    def bound_policies(cls, config: dict) -> set[str]:
        policies = config.get("policies")
        if policies:
            return set(policies.values())
        return {config["policy"]} if config.get("policy") else set()

    @classmethod
    def apply_policy_override(cls, base_config: dict, action: str, policy_id: str) -> dict:
        allowed_fields = set(cls.Config.model_fields)
        raw = {key: deepcopy(value) for key, value in base_config.items() if key in allowed_fields}
        policies = dict(raw.get("policies") or {})
        if not policies and raw.get("policy"):
            raw["policy"] = policy_id
        else:
            raw.pop("policy", None)
            policies[action] = policy_id
            raw["policies"] = policies
        return cls.Config.model_validate(raw).model_dump(mode="json")

    def init(self, ctx):
        if ctx.physics is None:
            raise ValueError(f"{self.id} requires the Newton PhysicsPort")
        interface = self.type or ctx._host.catalog.entities[ctx.key]["type"]
        config_dict = ctx.config.model_dump(mode="json")
        allowed = self.policy_joints(config_dict)
        self.joints = {key: ctx.physics.bind_joint(key) for key in allowed}
        self.base_body = ctx.physics.bind_body(ctx.config.baseBody) if ctx.config.baseBody else None
        self.slots: dict[str, PolicySlot] = {}
        if ctx.config.policies:
            for action, policy_id in ctx.config.policies.items():
                self.slots[action] = PolicySlot(
                    action,
                    policy_id,
                    entity_key=ctx.key,
                    interface=interface,
                    allowed_joints=allowed,
                )
        else:
            _, package = runtime_library().resolve(ctx.config.policy)
            task = read_json(package / "task.json")
            action = task["implements"].rsplit(".", 1)[1]
            self.slots[action] = PolicySlot(
                action,
                ctx.config.policy,
                entity_key=ctx.key,
                interface=interface,
                allowed_joints=allowed,
            )
        self.handle = None
        self.active_slot: PolicySlot | None = None
        self._last_slot: PolicySlot | None = next(iter(self.slots.values())) if len(self.slots) == 1 else None
        self.references: dict[str, str] = {}
        self.started = 0.0
        self.next_inference = 0.0
        self._init_robot(ctx)
        self._facts(ctx)
        self._set_idle_state(ctx)

    def _init_robot(self, ctx):
        return None

    def _facts(self, ctx):
        return None

    def _references(self, inv, ctx, slot: PolicySlot) -> dict[str, str]:
        return {}

    def _command(self, inv, ctx, slot: PolicySlot) -> list[float] | None:
        return None

    def _accept_stop(self, inv, ctx) -> Reject | None:
        return None

    def _accept_action(self, inv, ctx, slot: PolicySlot) -> Reject | None:
        return None

    def _action_timeout(self, inv, ctx, slot: PolicySlot) -> float:
        return float(ctx.config.maxSeconds)

    def _fault_failure(self, inv, ctx, slot: PolicySlot) -> tuple[str, str] | None:
        return None

    def _set_idle_state(self, ctx):
        if self.state_property is not None:
            ctx.set(**{self.state_property: self.idle_state})

    def _set_active_state(self, inv, ctx, slot: PolicySlot):
        if self.state_property is not None:
            ctx.set(**{self.state_property: self.active_state})

    def _set_fault_state(self, ctx, code: str, message: str):
        if self.state_property is not None:
            ctx.set(**{self.state_property: self.fault_state})

    def _on_action_start(self, inv, ctx, slot: PolicySlot):
        self._set_active_state(inv, ctx, slot)

    def _on_stop(self, inv, ctx):
        self._set_idle_state(ctx)

    def _hold(self, ctx):
        for ref in self.joints.values():
            ctx.physics.set_joint_target(ref, ctx.physics.joint_q(ref))

    def accept(self, inv, ctx):
        if inv.action in self.stop_actions:
            return self._accept_stop(inv, ctx)
        slot = self.slots.get(inv.action)
        if slot is None:
            return Reject(
                code="precondition_failed",
                message=f"No Policy is bound for action '{inv.action}'",
                details={"boundActions": sorted(self.slots)},
            )
        failure = coverage_failure(slot.task, inv.action, inv.params)
        if failure:
            return Reject(**failure)
        try:
            self._references(inv, ctx, slot)
            cmd = self._command(inv, ctx, slot)
        except (ValueError, KeyError) as exc:
            return Reject(code="precondition_failed", message=str(exc))
        if cmd is not None:
            cmd_failure = command_envelope_failure(slot.task, cmd)
            if cmd_failure:
                return Reject(**cmd_failure)
        return self._accept_action(inv, ctx, slot)

    def start(self, inv, ctx):
        if inv.action in self.stop_actions:
            self._hold(ctx)
            if self.handle is not None:
                self.handle.cancelled()
                self.handle = None
                self.active_slot = None
            self._on_stop(inv, ctx)
            inv.succeed()
            return
        slot = self.slots[inv.action]
        self.references = self._references(inv, ctx, slot)
        slot.action_starts += 1
        slot.previous[:] = 0.0
        self.active_slot = slot
        self._last_slot = slot
        self.handle = inv
        self.started = ctx.sim_time
        self.next_inference = ctx.sim_time
        self._on_action_start(inv, ctx, slot)

    def cancel(self, inv, ctx):
        self._hold(ctx)
        inv.cancelled()
        self.handle = None
        self.active_slot = None
        self._set_idle_state(ctx)

    def tick(self, ctx, dt):
        if ctx.fact_due:
            self._facts(ctx)
        inv, slot = self.handle, self.active_slot
        if inv is None or slot is None:
            return
        fault = self._fault_failure(inv, ctx, slot)
        if fault is not None:
            code, message = fault
            self._hold(ctx)
            inv.fail(code, message)
            self.handle = None
            self.active_slot = None
            self._set_fault_state(ctx, code, message)
            return
        if not ctx.check([slot.task["success"]]):
            self._hold(ctx)
            inv.succeed()
            self.handle = None
            self.active_slot = None
            self._set_idle_state(ctx)
            return
        if ctx.sim_time - self.started >= self._action_timeout(inv, ctx, slot):
            self._hold(ctx)
            inv.fail("motion_timeout", "Policy did not satisfy the pinned Task condition")
            self.handle = None
            self.active_slot = None
            self._set_idle_state(ctx)
            return
        interval = 1.0 / float(slot.task["control"]["hz"])
        if ctx.sim_time + 1e-10 < self.next_inference:
            return
        try:
            if dt > interval + 1e-10:
                raise ValueError("Runner control frequency is below the Task policy frequency")
            cmd = self._command(inv, ctx, slot)
            if cmd is not None:
                cmd_failure = command_envelope_failure(slot.task, cmd)
                if cmd_failure:
                    raise ValueError(cmd_failure["message"])
            observation = assemble(
                slot.spec,
                extract_observation_state(ctx.physics, slot.spec, self.references),
                previous_action=slot.previous,
                command=cmd,
                references=self.references,
            )
            slot.observation_digest.update(observation.tobytes())
            output = slot.model.infer(observation)
            positions = [ctx.physics.joint_q(self.joints[key])[0] for key in slot.spec.action.jointKeys]
            targets = joint_targets(slot.spec, positions, output)
            for key, value in zip(slot.spec.action.jointKeys, targets, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])
            slot.previous = output.copy()
            self.next_inference += interval
        except (ValueError, KeyError, RuntimeError) as exc:
            self._hold(ctx)
            inv.fail("policy_inference_failed", str(exc))
            self.handle = None
            self.active_slot = None
            self._set_fault_state(ctx, "policy_inference_failed", str(exc))

    def execution_evidence_for_action(self, action: str) -> dict | None:
        slot = self.slots.get(action)
        return slot.evidence() if slot is not None else None

    def execution_evidence(self) -> dict:
        slot = self.active_slot or self._last_slot or next(iter(self.slots.values()))
        return slot.evidence()
