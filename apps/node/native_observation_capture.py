"""Copy actual native ONNX-boundary inputs; a Task supplies only reviewed physical state."""

import inspect
import json
import math
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

if __package__:
    from .observation_capture import canonical_hash, code_hash, file_hash, task_file
else:
    from lw_observation_capture import canonical_hash, code_hash, file_hash, task_file


def source_identity(value):
    target = value if inspect.isclass(value) or inspect.ismethod(value) else type(value)
    target = inspect.unwrap(target)
    path = inspect.getsourcefile(target)
    if path is None:
        raise ValueError("Native source implementation is unavailable")
    return {"module": target.__module__, "qualname": target.__qualname__, "sha256": code_hash(path)}


def selected_rows(value, indices):
    if len(value.shape) != 2 or str(value.dtype) not in ("torch.float32", "float32"):
        raise ValueError("Native ONNX-boundary values must be float32 matrices")
    rows = value[indices].detach().to(device="cpu").tolist()
    if any(not math.isfinite(number) for row in rows for number in row):
        raise ValueError("Native ONNX-boundary values must be finite")
    return rows


class NativeObservationCapture:
    """Observe one approved native profile without changing actions or a training call."""

    def __init__(self, runner, observer, profile, inputs):
        self.runner, self.observer, self.inputs = runner, observer, inputs
        self.profile = json.loads(json.dumps(profile, allow_nan=False))
        self.samples, self.witnesses, self.unique = [], [], set()
        self.previous, self.previous_scopes, self.original, self.handle = None, None, None, None
        self.collecting, self.error, self.runtime = False, None, None

    def __enter__(self):
        try:
            policy = self.runner.alg.get_policy()
            self.policy = policy
            if self.observer.env is None or self.observer.disabled:
                raise ValueError("Native capture requires a valid owned canonical environment observer")
            self.indices = list(self.observer.selected)
            if not 1 <= len(self.indices) <= 4:
                raise ValueError("Native capture needs one to four actual environment indices")
            self.provider = self.observer.env.lw_policy_observation_state
            self.runtime = {
                "collectorSha256": code_hash(__file__),
                "runner": source_identity(self.runner),
                "actor": source_identity(policy),
                "act": source_identity(self.runner.alg.act),
                "exporter": source_identity(self.runner.export_policy_to_onnx),
                "stateProvider": source_identity(self.provider),
                "observationGroups": list(policy.obs_groups),
                "boundary": "rsl-mlp-pre-normalizer",
                "normalization": "inside-exported-graph",
                "previousAction": "raw-native-algorithm-output",
                "previousActionReset": "zero-on-environment-epoch",
            }
            for key in self.runtime:
                if self.profile[key] != self.runtime[key]:
                    raise ValueError("Actual native implementation differs from its reviewed profile: " + key)
            self.normalizer = policy.obs_normalizer
            self.handle = self.normalizer.register_forward_pre_hook(self._capture)
            algorithm = self.runner.alg
            self.owned_attribute = "act" in vars(algorithm)
            self.original = algorithm.act

            def act(*args, **kwargs):
                self.collecting = True
                try:
                    result = self.original(*args, **kwargs)
                finally:
                    self.collecting = False
                if self.error is None and len(self.samples) < 256:
                    try:
                        self.previous = selected_rows(result, self.indices)
                        self.previous_scopes = [
                            (epoch, self.observer.physics_count) for epoch in self.observer.epochs
                        ]
                    except Exception as error:
                        self.error = str(error)
                return result

            algorithm.act = act
        except Exception as error:
            self.error = str(error)
        return self

    def _capture(self, _, arguments):
        if not self.collecting or self.error is not None or self.previous is None or len(self.samples) >= 256:
            return
        try:
            step = self.observer.physics_count
            if (
                self.observer.env.sim.get_physics_step_count() != step
                or list(self.policy.obs_groups) != self.runtime["observationGroups"]
            ):
                raise ValueError("Native actor/physics scope changed before capture")
            if len(arguments) != 1:
                raise ValueError("Native actor normalization boundary has an unsupported call shape")
            vectors = selected_rows(arguments[0], self.indices)
            states = self.provider(self.indices)
            if self.observer.env.sim.get_physics_step_count() != step:
                raise ValueError("Native physics advanced while actor input was captured")
            if not isinstance(states, list) or len(states) != len(self.indices):
                raise ValueError("Native state provider must map exactly the selected physical states")
            for slot, (index, vector, physical) in enumerate(zip(self.indices, vectors, states, strict=True)):
                if not isinstance(physical, dict) or set(physical) - {"state", "references"}:
                    raise ValueError("Native state provider cannot supply actor vectors or action history")
                if (
                    len(vector) != self.profile["inputWidth"]
                    or len(self.previous[slot]) != self.profile["actionWidth"]
                ):
                    raise ValueError("Actual native dimensions differ from the exported policy")
                snapshot = json.loads(json.dumps(physical, allow_nan=False))
                epoch, step = self.observer.epochs[slot], self.observer.physics_count
                previous_epoch, previous_step = self.previous_scopes[slot]
                if epoch == previous_epoch and step <= previous_step:
                    continue  # No new actual physics state has consumed that action yet.
                previous = (
                    self.previous[slot] if epoch == previous_epoch else [0.0] * self.profile["actionWidth"]
                )
                sample = {
                    "state": snapshot["state"],
                    "references": snapshot.get("references", {}),
                    "previousAction": previous,
                    "trainingObservation": vector,
                }
                state_hash = canonical_hash(
                    {key: sample[key] for key in ("state", "references", "previousAction")}
                )
                if state_hash in self.unique:
                    continue
                identity = f"env-{index}/epoch-{epoch}/step-{step}"
                if any(row["sourceStateId"] == identity for row in self.samples):
                    raise ValueError("Actual native sampling scope did not advance")
                sample["sourceStateId"] = identity
                self.samples.append(sample)
                self.unique.add(state_hash)
                self.witnesses.append(
                    {
                        "sourceStateId": identity,
                        "environment": index,
                        "epoch": epoch,
                        "physicsStep": step,
                        "stateSha256": state_hash,
                        "nativeInputSha256": canonical_hash(vector),
                    }
                )
                if len(self.samples) == 256:
                    break
        except Exception as error:
            self.error = str(error)

    def __exit__(self, *exception):
        if self.original is not None:
            if self.owned_attribute:
                self.runner.alg.act = self.original
            else:
                delattr(self.runner.alg, "act")
        if self.handle is not None:
            self.handle.remove()

    def publish(self, output, export, versions):
        directory = Path(output) / "observations"
        directory.mkdir(exist_ok=True)
        if self.error is not None or len(self.samples) < 256:
            value = {
                "schema": "lw.native-observation-unavailable/1",
                "profile": self.profile["id"],
                "reason": self.error or "Fewer than256 distinct actual native states were captured",
                "samples": len(self.samples),
            }
            (directory / "native-unavailable.json").write_text(json.dumps(value), encoding="utf-8")
            return None
        job = self.inputs["job"]
        if not job.get("leaseId"):
            raise ValueError("Native capture requires a fenced source Job")
        source = {
            key: job[key] for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
        }
        source["attemptId"] = "attempt-" + sha256(f"{job['jobId']}:{job['leaseId']}".encode()).hexdigest()
        source["versions"] = versions
        package = Path(os.environ["LW_TASK_PACKAGE"])
        weights = {
            "checkpointSha256": export["checkpointSha256"],
            "onnxSha256": export["policySha256"],
            "observationSha256": file_hash(task_file(package, self.inputs["task"]["observation"])),
        }
        capture = {
            "producer": "native-onnx-input/1",
            "profile": self.profile["id"],
            "profileSha256": canonical_hash(self.profile),
            "runtime": self.runtime,
            "capturedAt": datetime.now(UTC).isoformat(),
        }
        envelope = {
            "schema": "lw.policy-native-observation-samples/1",
            "source": source,
            "policy": weights,
            "capture": capture,
            "samples": self.samples,
        }
        samples = directory / "native-samples.json"
        samples.write_text(json.dumps(envelope, sort_keys=True, allow_nan=False), encoding="utf-8")
        normalizer = {
            key: value.detach().to(device="cpu").tolist()
            for key, value in self.normalizer.state_dict().items()
        }
        receipt = {
            "schema": "lw.policy-native-observation-capture/1",
            "source": source,
            "policy": weights,
            "capture": capture,
            "samplesArtifact": "observations/native-samples.json",
            "samplesSha256": file_hash(samples),
            "sampleCount": len(self.samples),
            "witnesses": self.witnesses,
            "normalizerStateSha256": canonical_hash(normalizer),
            "afterLearn": export["afterLearn"],
            "iteration": export["iteration"],
        }
        (directory / "native-capture.json").write_text(
            json.dumps(receipt, sort_keys=True, allow_nan=False), encoding="utf-8"
        )
        return receipt
