"""Reusable CPU ONNX multi-policy Device Program base for general embodied robots."""

from __future__ import annotations

import hashlib
import math
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
    remap_observation_spec_entity,
    spec_within_robot_entity,
)

POLICY_REF = r"^policy/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$"
ActionName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9]*$")]
ParamName = Annotated[str, Field(min_length=1)]
PolicyRef = Annotated[str, Field(pattern=POLICY_REF)]
SpatialPath = Annotated[str, Field(min_length=1)]


def inspect_port_joints(
    port,
    joint_refs: dict[str, object],
    *,
    fallback_units: dict[str, str],
    prismatic_keys: list[str] | None = None,
) -> tuple[dict[str, str], dict[str, tuple[float | None, float | None]]]:
    """Derive actual joint units and finite physical limits from PhysicsPort when available."""
    if not hasattr(port, "joint_metadata"):
        return dict(fallback_units), {key: (None, None) for key in joint_refs}
    units: dict[str, str] = {}
    limits: dict[str, tuple[float | None, float | None]] = {}
    for key, ref in joint_refs.items():
        meta = port.joint_metadata(ref)
        kind = meta.get("kind")
        if kind not in ("revolute", "prismatic") or any(
            meta.get(width) != 1 for width in ("qWidth", "qdWidth", "targetWidth")
        ):
            raise ValueError(f"Joint {key} must be a scalar revolute or prismatic drive")
        units[key] = "m" if kind == "prismatic" else "rad"
        raw_lo, raw_hi = meta.get("lower"), meta.get("upper")
        lo = float(raw_lo) if isinstance(raw_lo, int | float) and math.isfinite(raw_lo) else None
        hi = float(raw_hi) if isinstance(raw_hi, int | float) and math.isfinite(raw_hi) else None
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"Joint {key} has invalid physical limits [{lo}, {hi}]")
        limits[key] = (lo, hi)
    if prismatic_keys:
        actual_prismatic = {key for key, unit in units.items() if unit == "m"}
        if set(prismatic_keys) != actual_prismatic:
            raise ValueError("prismaticJointKeys does not match actual prismatic joints")
    return units, limits


def validate_action_against_joints(
    action_spec,
    units: dict[str, str],
    limits: dict[str, tuple[float | None, float | None]],
) -> None:
    if not set(action_spec.jointKeys) <= set(units):
        raise ValueError("Policy action targets must belong to the configured robot joints")
    expected_units = [units[key] for key in action_spec.jointKeys]
    if action_spec.units != expected_units:
        raise ValueError("Policy action units differ from the physical robot joint kinds")
    for key, act_lo, act_hi in zip(
        action_spec.jointKeys, action_spec.lower, action_spec.upper, strict=True
    ):
        phys_lo, phys_hi = limits.get(key, (None, None))
        if (phys_lo is not None and act_lo < phys_lo - 1e-6) or (
            phys_hi is not None and act_hi > phys_hi + 1e-6
        ):
            raise ValueError(f"Policy action bounds exceed physical joint limits on {key}")


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
        if item.frame is not None and item.frame not in ("world", "body") and source != "command":
            ensure_pose("bodies", item.frame)
    return state


def step_policy_inference(
    port,
    joints: dict[str, object],
    spec: ObservationSpec,
    model: CpuPolicy,
    previous: np.ndarray,
    observation_digest,
    *,
    references: dict[str, str] | None = None,
    command: list[float] | None = None,
    joint_limits: dict[str, tuple[float | None, float | None]] | None = None,
) -> np.ndarray:
    """Execute one CPU ONNX policy step, clamping targets to finite physical joint limits."""
    refs = references or {}
    observation = assemble(
        spec,
        extract_observation_state(port, spec, refs),
        previous_action=previous,
        command=command,
        references=refs,
    )
    observation_digest.update(observation.tobytes())
    output = model.infer(observation)
    positions = [port.joint_q(joints[key])[0] for key in spec.action.jointKeys]
    targets = joint_targets(spec, positions, output)
    for key, raw_target in zip(spec.action.jointKeys, targets, strict=True):
        value = float(raw_target)
        if joint_limits is not None and key in joint_limits:
            phys_lo, phys_hi = joint_limits[key]
            if phys_lo is not None:
                value = max(phys_lo, value)
            if phys_hi is not None:
                value = min(phys_hi, value)
        port.set_joint_target(joints[key], [value])
    # previous_action always means raw model output, before action scale/clipping.
    return output.copy()


def build_policy_evidence(record: dict, model: CpuPolicy, action_starts: int, observation_digest) -> dict:
    """Read-only native receipt; all counts originate at the actual CPU runtime."""
    return {
        "kind": "policy",
        "policy": record["id"],
        "policyContentHash": record["contentHash"],
        "onnxSha256": record["onnxSha256"],
        "observationSha256": record["observationSha256"],
        "providers": model.session.get_providers(),
        "runtimeVersion": model.runtime_version,
        "actionStarts": action_starts,
        "inferenceCalls": model.inference_calls,
        "validOutputs": model.valid_outputs,
        "observationDigest": observation_digest.hexdigest(),
    }


def validate_observations_against_port(spec: ObservationSpec, port) -> None:
    """Verify every canonical joint, body, and site in spec.observations exists on PhysicsPort."""
    for item in spec.observations:
        source = item.source
        if source.startswith("joint_"):
            for key in item.keys:
                if not key.startswith("/"):
                    continue
                try:
                    ref = port.bind_joint(key)
                except (KeyError, AssertionError, ValueError) as exc:
                    raise ValueError(f"Observation joint {key} is unavailable on PhysicsPort") from exc
                if hasattr(port, "joint_metadata"):
                    meta = port.joint_metadata(ref)
                    if meta.get("kind") not in ("revolute", "prismatic") or any(
                        meta.get(width) != 1 for width in ("qWidth", "qdWidth")
                    ):
                        raise ValueError(
                            f"Observation joint {key} must be a scalar revolute or prismatic joint"
                        )
        elif source == "projected_gravity" or source.startswith("body_"):
            if item.key is not None and item.key.startswith("/"):
                try:
                    port.bind_body(item.key)
                except (KeyError, AssertionError, ValueError) as exc:
                    raise ValueError(f"Observation body {item.key} is unavailable on PhysicsPort") from exc
        elif source.startswith("site_"):
            if item.key is not None and item.key.startswith("/"):
                try:
                    if hasattr(port, "bind_site"):
                        port.bind_site(item.key)
                    elif hasattr(port, "site_pose"):
                        port.site_pose(item.key)
                    else:
                        raise ValueError("PhysicsPort has no site bindings")
                except (KeyError, AssertionError, ValueError) as exc:
                    raise ValueError(f"Observation site {item.key} is unavailable on PhysicsPort") from exc
        if item.frame is not None and item.frame.startswith("/") and source != "command":
            try:
                port.bind_body(item.frame)
            except (KeyError, AssertionError, ValueError) as exc:
                raise ValueError(
                    f"Observation frame body {item.frame} is unavailable on PhysicsPort"
                ) from exc


class PolicySlot:
    """Isolated runtime state and native CPU ONNX execution counters for one bound action."""

    def __init__(
        self,
        action: str,
        policy_id: str,
        *,
        entity_key: str,
        interface: str,
        joint_units: dict[str, str],
        joint_limits: dict[str, tuple[float | None, float | None]],
        physics=None,
    ):
        self.action = action
        self.record, self.package = runtime_library().resolve(policy_id)
        self.task = read_json(self.package / "task.json")
        raw_spec = ObservationSpec.model_validate(document(self.package / "obs_spec.yaml"))
        if self.task["implements"] != f"{interface}.{action}":
            raise ValueError("Policy Task does not identify this robot entity and bound action")
        task_robot = self.task["robot"]
        if task_robot != entity_key and not spec_within_robot_entity(raw_spec, task_robot):
            raise ValueError("Policy Task does not identify this robot entity and bound action")
        self.spec = remap_observation_spec_entity(raw_spec, task_robot, entity_key)
        validate_action_against_joints(self.spec.action, joint_units, joint_limits)
        if physics is not None:
            validate_observations_against_port(self.spec, physics)
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
        return build_policy_evidence(self.record, self.model, self.action_starts, self.observation_digest)


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
    commandParams: dict[ActionName, list[ParamName]] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
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
        for action, params in self.commandParams.items():
            if not params or len(set(params)) != len(params):
                raise ValueError(f"commandParams[{action}] requires distinct parameter names")
        return self


class MultiPolicyProgram:
    """Subclassable multi-action CPU ONNX policy program over Newton PhysicsPort."""

    policy_program = True
    evaluation_implementation_role = "fixed-runtime"
    Config = MultiPolicyConfig

    @classmethod
    def supports_type(cls, entity_type: str) -> bool:
        return cls.type is None or cls.type == entity_type

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
        interface = self.type or ctx.entity_type
        config_dict = ctx.config.model_dump(mode="json")
        fallback_units = self.policy_joints(config_dict)
        self.joints = {key: ctx.physics.bind_joint(key) for key in fallback_units}
        self.joint_units, self.joint_limits = inspect_port_joints(
            ctx.physics,
            self.joints,
            fallback_units=fallback_units,
            prismatic_keys=ctx.config.prismaticJointKeys,
        )
        self.base_body = ctx.physics.bind_body(ctx.config.baseBody) if ctx.config.baseBody else None
        self.slots: dict[str, PolicySlot] = {}
        if ctx.config.policies:
            for action, policy_id in ctx.config.policies.items():
                self.slots[action] = PolicySlot(
                    action,
                    policy_id,
                    entity_key=ctx.key,
                    interface=interface,
                    joint_units=self.joint_units,
                    joint_limits=self.joint_limits,
                    physics=ctx.physics,
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
                joint_units=self.joint_units,
                joint_limits=self.joint_limits,
                physics=ctx.physics,
            )
        self._command_keys: dict[str, tuple[str, ...]] = {}
        if type(self)._command is MultiPolicyProgram._command:
            for action, slot in self.slots.items():
                declared_params = ctx.action_params(action)
                cmd_obs = [item for item in slot.spec.observations if item.source == "command"]
                mapped = ctx.config.commandParams.get(action)
                if mapped is not None and not set(mapped) <= set(declared_params):
                    raise ValueError(f"commandParams[{action}] references undeclared action parameters")
                if cmd_obs:
                    chosen = tuple(mapped) if mapped is not None else declared_params
                    if not chosen or any(len(chosen) != item.shape[0] for item in cmd_obs):
                        raise ValueError(
                            f"Action '{action}' requires commandParams matching its command observation width"
                        )
                    self._command_keys[action] = chosen
                elif mapped is not None:
                    if slot.task.get("episode", {}).get("commandEnvelope") is None:
                        raise ValueError(
                            f"commandParams configured for action '{action}' without command observation"
                        )
                    self._command_keys[action] = tuple(mapped)
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
        params = self._command_keys.get(inv.action)
        if params is None:
            return None
        return [float(inv.params[name]) for name in params]

    def _accept_stop(self, inv, ctx) -> Reject | None:
        return None

    def _parameter_coverage_failure(self, inv, ctx, slot: PolicySlot) -> dict | None:
        return coverage_failure(slot.task, inv.action, inv.params)

    def _accept_action(self, inv, ctx, slot: PolicySlot) -> Reject | None:
        return None

    def _action_timeout(self, inv, ctx, slot: PolicySlot) -> float:
        return float(ctx.config.maxSeconds)

    def _fault_failure(self, inv, ctx, slot: PolicySlot) -> tuple[str, str] | None:
        return None

    def _action_completed(self, inv, ctx, slot: PolicySlot) -> bool:
        return not ctx.check([slot.task["success"]])

    def _tick_robot(self, ctx, dt: float) -> None:
        return None

    def _step_active_action(self, inv, ctx, slot: PolicySlot, dt: float) -> bool:
        return False

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

    def _on_action_complete(self, inv, ctx, slot: PolicySlot):
        self._set_idle_state(ctx)

    def _on_timeout(self, inv, ctx, slot: PolicySlot):
        self._set_idle_state(ctx)

    def _on_cancel(self, inv, ctx):
        self._set_idle_state(ctx)

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
        failure = self._parameter_coverage_failure(inv, ctx, slot)
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
        self._on_cancel(inv, ctx)

    def tick(self, ctx, dt):
        self._tick_robot(ctx, dt)
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
        if self._action_completed(inv, ctx, slot):
            self._hold(ctx)
            self.handle = None
            self.active_slot = None
            self._on_action_complete(inv, ctx, slot)
            inv.succeed()
            return
        if ctx.sim_time - self.started + 1e-10 >= self._action_timeout(inv, ctx, slot):
            self._hold(ctx)
            inv.fail("motion_timeout", "Policy did not satisfy the pinned Task condition")
            self.handle = None
            self.active_slot = None
            self._on_timeout(inv, ctx, slot)
            return
        if self._step_active_action(inv, ctx, slot, dt):
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
            slot.previous = step_policy_inference(
                ctx.physics,
                self.joints,
                slot.spec,
                slot.model,
                slot.previous,
                slot.observation_digest,
                references=self.references,
                command=cmd,
                joint_limits=self.joint_limits,
            )
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
