"""Standalone trainer-side view adapter; copied beside the zero-argument callback.

Only this module enters Isaac/Warp. The Node owns authentication and transport.
"""

import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
import queue
import threading
import time
import uuid
from pathlib import Path


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def transform(value):
    if len(value) != 7 or not all(math.isfinite(v) for v in value):
        raise ValueError("A mapping transform must contain seven finite values")
    if abs(sum(v * v for v in value[3:]) - 1) > 1e-4:
        raise ValueError("A mapping quaternion must have unit length")
    return value


def validate_mapping(mapping, inputs, labels, worlds, num_envs):
    """Validate actual model membership once; never infer clone layout or body stride."""
    job, hint = inputs["job"], inputs["view"]
    if mapping.get("schema") != "lw.training-view-map/1":
        raise ValueError("Task must supply lw.training-view-map/1")
    for name in ("taskContentHash", "sceneContentHash"):
        if mapping.get(name) != job[name]:
            raise ValueError("Task mapping differs from the approved " + name)
    if sorted(mapping.get("assets", []), key=lambda row: row["id"]) != sorted(
        job["assets"], key=lambda row: row["id"]
    ):
        raise ValueError("Task mapping differs from the approved Asset closure")
    if not isinstance(mapping.get("solver"), str) or not mapping["solver"].strip():
        raise ValueError("Task mapping must identify its actual solver")
    selected = list(range(min(num_envs, 4)))
    environments = mapping.get("environments", [])
    if [row["envIndex"] for row in environments] != selected or not selected:
        raise ValueError("Task mapping must cover the selected real environments 0–3 exactly")
    expected = {row["entityKey"] for row in hint["bodyTable"]}
    if len(expected) != len(hint["bodyTable"]):
        raise ValueError("Geometry body table contains duplicate canonical keys")
    for environment in environments:
        transform(environment["cloneTransform"])
        rows = environment["bodies"]
        if len(rows) != len(expected) or {row["entityKey"] for row in rows} != expected:
            raise ValueError("Task mapping must cover every canonical Lab body exactly once")
        for row in rows:
            index = row["bodyIndex"]
            if (
                type(index) is not int
                or not 0 <= index < len(labels)
                or labels[index] != row["bodyLabel"]
                or int(worlds[index]) != environment["world"]
            ):
                raise ValueError("Task mapping does not match actual body label/world membership")
            transform(row["localTransform"])
    if len({row["world"] for row in environments}) != len(selected):
        raise ValueError("Selected environments must identify distinct actual worlds")
    return selected


def gather_kernel(wp):
    # Define lazily: importing this helper before AppLauncher must not import Warp.
    @wp.kernel
    def gather(
        poses: wp.array(dtype=wp.transform),
        indices: wp.array(dtype=wp.int32),
        local: wp.array(dtype=wp.transform),
        inverse: wp.array(dtype=wp.transform),
        output: wp.array(dtype=wp.transform),
        bodies: int,
    ):
        i = wp.tid()
        output[i] = wp.transform_multiply(
            inverse[i // bodies], wp.transform_multiply(poses[indices[i]], local[i])
        )

    return gather


class LatestState:
    """A single pending immutable sample and one replaceable IPC file; no history."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.queue = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._write, name="training-view-ipc", daemon=True)
        self.thread.start()

    def put(self, value):
        try:
            self.queue.put_nowait(value)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(value)
            except queue.Full:
                pass

    def _write(self):
        while not self.stopped.is_set():
            try:
                value = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if "poses" in value:
                    poses = value.pop("poses").tolist()
                    for frame, rows in zip(value["frames"], poses, strict=True):
                        for row, pose in zip(frame["payload"]["poses"], rows, strict=True):
                            row.extend(pose)
                temporary = self.directory / "latest.tmp"
                temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
                os.replace(temporary, self.directory / "latest.json")
            except (OSError, ValueError):
                # View-only failure; retry the next latest immutable sample.
                continue

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=3)
        if not self.thread.is_alive():
            for name in ("latest.json", "latest.tmp"):
                with contextlib.suppress(OSError):
                    (self.directory / name).unlink(missing_ok=True)


class TrainingViewBridge:
    def __init__(self, runner, inputs, *, clock=time.monotonic):
        self.runner, self.inputs, self.clock = runner, inputs, clock
        self.writer = None
        self.env = None
        self.originals = {}
        self.disabled = False
        self.view_id = "view-" + uuid.uuid4().hex

    def envelope(self, kind, payload, index=None):
        value = {
            "schema": "lw.training-view/1", "type": kind,
            "jobId": self.inputs["job"]["jobId"],
            "attemptId": self.inputs.get("view", {}).get("attemptId"),
            "viewId": self.view_id, "payload": payload,
        }
        if index is not None:
            value["envIndex"] = index
        return value

    def error(self, error, code="training_view_mapping_unavailable"):
        self.disabled = True
        if self.writer:
            self.writer.put({"error": self.envelope("error", {"code": code, "message": str(error)[:4000]})})

    def __enter__(self):
        directory = os.getenv("LW_NODE_VIEW_IPC")
        if not directory:
            return self  # Older Node launches keep their original training behavior.
        try:
            self.writer = LatestState(directory)
            hint = self.inputs.get("view", {})
            if "error" in hint:
                self.error(hint["error"]["message"], hint["error"]["code"])
                return self
            import torch
            import warp as wp
            from isaaclab_newton.physics import NewtonManager

            self.wp, self.torch, self.manager = wp, torch, NewtonManager
            self.env = self.runner.env.unwrapped
            model = NewtonManager.get_model()
            mapping = self.env.lw_training_view()
            self.selected = validate_mapping(
                mapping, self.inputs, model.body_label, model.body_world.numpy(), self.env.num_envs
            )
            self.table = hint["bodyTable"]
            indices, local, inverse = [], [], []
            for environment in mapping["environments"]:
                rows = {row["entityKey"]: row for row in environment["bodies"]}
                for body in self.table:
                    row = rows[body["entityKey"]]
                    indices.append(row["bodyIndex"])
                    local.append(wp.transform(row["localTransform"][:3], row["localTransform"][3:]))
                clone = environment["cloneTransform"]
                inverse.append(wp.transform_inverse(wp.transform(clone[:3], clone[3:])))
            device = model.device
            self.indices = wp.array(indices, dtype=wp.int32, device=device)
            self.local = wp.array(local, dtype=wp.transform, device=device)
            self.inverse = wp.array(inverse, dtype=wp.transform, device=device)
            self.output = wp.empty(len(indices), dtype=wp.transform, device=device)
            self.kernel = gather_kernel(wp)
            self.epochs, self.sequences = [0] * len(self.selected), [0] * len(self.selected)
            self.dt = self.env.sim.get_physics_dt()
            self.physics_count = self.env.sim.get_physics_step_count()
            self.sim_time = self.physics_count * self.dt
            self.last_sample = -math.inf
            source = {
                "kind": "newton", "newtonVersion": importlib.metadata.version("newton"),
                "solver": mapping["solver"], "physicsDt": self.dt, "basePublishHz": 10.0,
                "realtimeFactor": None, "sampleTimeClock": "runner-monotonic",
            }
            job = self.inputs["job"]
            self.hello = self.envelope("hello", {
                **{key: job[key] for key in (
                    "task", "taskContentHash", "scene", "sceneContentHash", "assets"
                )},
                "descriptorDigest": hint["descriptorDigest"], "mappingDigest": canonical_digest(mapping),
                "environments": self.selected, "source": source, "maxRateHz": 10.0,
            })
            for name in ("step", "reset"):
                # Restore instance attributes exactly, including inherited bound methods.
                self.originals[name] = (name in vars(self.env), getattr(self.env, name))
                setattr(self.env, name, self._observe(name, getattr(self.env, name)))
            self._sample()
        except Exception as error:
            self.error(error)
        return self

    def _observe(self, name, original):
        def observed(*args, **kwargs):
            result = original(*args, **kwargs)
            if not self.disabled:
                try:
                    if name == "reset":
                        self.epochs = [epoch + 1 for epoch in self.epochs]
                    else:
                        # DirectRLEnv.step returns (obs, rewards, terminated, truncated, extras).
                        done = result[2] | result[3]
                        selected = self.torch.tensor(self.selected, dtype=self.torch.long, device=done.device)
                        resets = done.index_select(0, selected).to(device="cpu").tolist()
                        self.epochs = [
                            epoch + int(reset) for epoch, reset in zip(self.epochs, resets, strict=True)
                        ]
                    count = self.env.sim.get_physics_step_count()
                    if count < self.physics_count:
                        raise ValueError("Actual physics step clock regressed")
                    self.sim_time += (count - self.physics_count) * self.dt
                    self.physics_count = count
                    self._sample()
                except Exception as error:
                    self.error(error)
            return result
        return observed

    def _sample(self):
        now = self.clock()
        if now - self.last_sample < 0.1:
            return
        wp = self.wp
        # Fresh public state on the stepping thread. Only bounded gathered output is copied.
        state = self.manager.get_state()
        wp.launch(self.kernel, dim=len(self.indices), inputs=[
            state.body_q, self.indices, self.local, self.inverse, self.output, len(self.table)
        ], device=self.output.device)
        poses = self.output.numpy().copy().reshape((len(self.selected), len(self.table), 7))
        frames = []
        for slot, index in enumerate(self.selected):
            frames.append(self.envelope("frame", {
                "protocolVersion": 2, "type": "stream.snapshot",
                "sessionId": f"training-view:{self.view_id}:env:{index}",
                "sceneRevision": "training-scene-" + self.inputs["view"]["descriptorDigest"],
                "epoch": self.epochs[slot], "seq": self.sequences[slot],
                "simTime": self.sim_time, "sampleTimeMs": now * 1000,
                "poses": [[row["streamId"]] for row in self.table],
            }, index))
            self.sequences[slot] += 1
        self.writer.put({"hello": self.hello, "frames": frames, "poses": poses})
        self.last_sample = now

    def __exit__(self, *exception):
        if self.env is not None:
            for name, (owned_attribute, original) in self.originals.items():
                if owned_attribute:
                    setattr(self.env, name, original)
                else:
                    delattr(self.env, name)
        if self.writer:
            with contextlib.suppress(Exception):
                self.writer.close()  # A failed view teardown cannot replace a training result/exception.


def observe_training(runner, inputs):
    return TrainingViewBridge(runner, inputs)
