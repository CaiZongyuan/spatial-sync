"""Copied into the trainer environment; imports only stdlib until Isaac calls install."""

import hashlib
import importlib.metadata
import json
import os
import statistics
import sys
import time
from functools import wraps
from pathlib import Path, PurePosixPath, PureWindowsPath

if __package__:
    from packages.training.runtime import rsl_rl_matches, trainer_distributions
else:
    from lw_training_runtime import rsl_rl_matches, trainer_distributions


def external_files(policy, output):
    import onnx

    model = onnx.load_model(str(policy), load_external_data=False)
    pending, files = [model], {}
    while pending:
        message = pending.pop()
        if (
            message.DESCRIPTOR.full_name == "onnx.TensorProto"
            and message.data_location == onnx.TensorProto.EXTERNAL
        ):
            properties = {item.key: item.value for item in message.external_data}
            location = properties["location"]
            relative = PurePosixPath(location)
            if (
                not location
                or "\\" in location
                or relative.is_absolute()
                or PureWindowsPath(location).drive
                or any(part in (".", "..") or ":" in part for part in relative.parts)
            ):
                raise ValueError("ONNX external data must use safe relative file paths")
            file = policy.parent / location
            if file.is_symlink() or not file.resolve().is_relative_to(output.resolve()):
                raise ValueError("ONNX external data escapes the owned output directory")
            name = file.relative_to(output).as_posix()
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files[name] = {"path": name, "sha256": digest, "size": file.stat().st_size}
        for field, value in message.ListFields():
            if field.cpp_type == field.CPPTYPE_MESSAGE:
                pending.extend(value if field.is_repeated else [value])
    return [files[name] for name in sorted(files)]


_ACTIVE_WRAP_LEARN = None


def ensure_runner_bridged(runner_cls):
    if _ACTIVE_WRAP_LEARN is None or runner_cls is None:
        return runner_cls
    current = getattr(runner_cls, "learn", None)
    if current is not None and not getattr(current, "_lw_bridged", False):
        runner_cls.learn = _ACTIVE_WRAP_LEARN(current)
    return runner_cls


def verify_trainer_versions(inputs, is_mjlab):
    actual = {
        name: importlib.metadata.version(distribution)
        for name, distribution in trainer_distributions(is_mjlab).items()
    }
    if not rsl_rl_matches(actual["rsl_rl"], inputs["plan"]["algorithm"].get("version")):
        raise RuntimeError("Installed RSL-RL version differs from the approved plan")
    if any(actual.get(name) != version for name, version in (inputs.get("trainerVersions") or {}).items()):
        raise RuntimeError("Installed trainer versions differ from the leased Compute Node capabilities")
    return actual


def install():
    global _ACTIVE_WRAP_LEARN
    from rsl_rl.runners import OnPolicyRunner

    output = Path(os.environ["LW_NODE_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    inputs = json.loads(Path(os.environ["LW_NODE_INPUTS"]).read_text(encoding="utf-8"))
    job = inputs["job"]
    portable = inputs.get("schemaVersion") == 2
    if portable:
        import lw_checkpoint_format as checkpoint_wire
        import lw_checkpoint_runner as checkpoint_runner

        if "checkpointIdentity" not in inputs:
            raise ValueError("Portable dispatch lacks checkpoint authority")
    declared = inputs.get("task", {})
    is_mjlab = isinstance(declared, dict) and declared.get("mjlab") is not None
    versions = {
        "versions": verify_trainer_versions(inputs, is_mjlab),
        "hardware": inputs["hardware"],
        "demonstrationSets": job.get("demonstrationSets", []),
        **{
            name: job[name]
            for name in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
        },
    }
    if declared.get("declarative") is not None:
        versions["runtimeIdentity"] = inputs["runtimeIdentity"]
    presets = (
        declared.get("isaaclab", {}).get("presets", [])
        if isinstance(declared, dict) and not is_mjlab
        else []
    )
    if not presets and not is_mjlab:
        presets = next(
            (arg.split("=", 1)[1].split(",") for arg in sys.argv if arg.startswith("presets=")), []
        )
    if (
        not is_mjlab
        and versions["hardware"]["device"] == "cpu"
        and versions["versions"]["isaaclab"] == "3.0.0"
        and versions["versions"]["newton"] == "1.6.0rc1"
        and "newton_mjwarp" in presets
    ):
        raise RuntimeError(
            "This frozen IsaacLab3/Newton1.6 native CPU runtime lacks contact reporting; "
            "no approved environment count or physics setting was changed"
        )
    (output / "versions.json").write_text(json.dumps(versions), encoding="utf-8")

    def _wrap_learn(raw_learn):
        @wraps(raw_learn)
        def learn(runner, *args, **kwargs):
            if getattr(runner, "_lw_in_bridged_learn", False):
                return raw_learn(runner, *args, **kwargs)
            from contextlib import ExitStack

            runner._lw_in_bridged_learn = True
            native = None
            original_save = runner.save if portable else None
            source = inputs.get("resumeCheckpoint")
            finalize = job["kind"] == "checkpoint_finalize" if portable else False
            completed = 0
            saved = None
            original_log = runner.logger.log
            started = time.monotonic()

            def log(*args, **kwargs):
                result = original_log(*args, **kwargs)
                row = {
                    "iteration": int(kwargs.get("it", runner.current_learning_iteration)) + 1,
                    "elapsedSeconds": time.monotonic() - started,
                    "meanReward": (
                        statistics.mean(runner.logger.rewbuffer) if runner.logger.rewbuffer else None
                    ),
                    "trainSuccess": None,
                    "memoryUsedMb": None,
                }
                with (output / "metrics.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
                return result

            def save(_path, infos=None):
                nonlocal completed, saved
                completed = runner.current_learning_iteration + 1
                if saved is None or saved[0] != completed:
                    saved = (completed, checkpoint_runner.publish(
                        Path.cwd() / "checkpoints-pending", runner, inputs["checkpointIdentity"], completed,
                    ))

            try:
                if portable:
                    if source:
                        raw = Path(inputs["checkpointPath"]).read_bytes()
                        metadata, _ = checkpoint_runner.restore(runner, raw, source)
                        completed = metadata["iteration"]
                    if not finalize:
                        runner.save = save
                        if args or "num_learning_iterations" not in kwargs:
                            raise ValueError("Managed checkpoint dispatch requires named iteration count")
                        kwargs["num_learning_iterations"] -= completed
                runner.logger.log = log
                try:
                    with ExitStack() as stack:
                        if os.getenv("LW_NODE_VIEW_IPC") or inputs.get("nativeObservationProfile"):
                            from lw_view_bridge import observe_training

                            observer = stack.enter_context(observe_training(runner, inputs))
                            if profile := inputs.get("nativeObservationProfile"):
                                if profile.get("compilerHash"):
                                    from lw_declarative_capture import (
                                        DeclarativeCapture as NativeObservationCapture,
                                    )
                                else:
                                    from lw_native_observation_capture import NativeObservationCapture

                                native = stack.enter_context(
                                    NativeObservationCapture(runner, observer, profile, inputs)
                                )
                        if finalize or portable and kwargs["num_learning_iterations"] == 0:
                            if finalize or native is not None:
                                checkpoint_runner.collect_without_updates(runner, native)
                            result = None
                        else:
                            result = raw_learn(runner, *args, **kwargs)
                finally:
                    runner.logger.log = original_log
                if portable:
                    if not finalize and completed != inputs["plan"]["hyperparameters"]["max_iterations"]:
                        raise ValueError("Training ended before the approved iteration limit")
                    raw = saved[1] if saved else Path(inputs["checkpointPath"]).read_bytes()
                    metadata, tensors, _, _ = checkpoint_wire.read_checkpoint(raw)
                    relative = "checkpoint.safetensors"
                    (output / relative).write_bytes(tensors)
                    (output / "checkpoint.json").write_bytes(checkpoint_wire.json_bytes(metadata))
                    checkpoint_hash = metadata["tensorSha256"]
                    export_iteration = metadata["iteration"]
                else:
                    checkpoint = Path(runner.logger.log_dir) / f"model_{runner.current_learning_iteration}.pt"
                    relative = "checkpoints/" + checkpoint.resolve().relative_to(
                        (Path.cwd() / "logs").resolve(),
                    ).as_posix()
                    with checkpoint.open("rb") as stream:
                        checkpoint_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                    export_iteration = runner.current_learning_iteration
                try:
                    import torch.onnx as _torch_onnx
                except ImportError:
                    _torch_onnx = None

                original_onnx_export = getattr(_torch_onnx, "export", None)
                if _torch_onnx is not None and original_onnx_export is not None:
                    def _legacy_onnx_export(*export_args, **export_kwargs):
                        export_kwargs["dynamo"] = False
                        export_kwargs["dynamic_axes"] = {}
                        return original_onnx_export(*export_args, **export_kwargs)

                    _torch_onnx.export = _legacy_onnx_export
                try:
                    runner.export_policy_to_onnx(str(output), filename="policy.onnx")
                finally:
                    if _torch_onnx is not None and original_onnx_export is not None:
                        _torch_onnx.export = original_onnx_export
                with (output / "policy.onnx").open("rb") as stream:
                    policy_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                export = {
                    "checkpoint": relative,
                    "checkpointSha256": checkpoint_hash,
                    "policy": "policy.onnx",
                    "policySha256": policy_hash,
                    "externalData": external_files(output / "policy.onnx", output),
                    "iteration": export_iteration,
                    "afterLearn": not finalize,
                }
                if portable:
                    export.update(schemaVersion=2, checkpointArchiveSha256=checkpoint_wire.digest(raw))
                elif inputs.get("keepLegacyCheckpointLocal"):
                    origin = {
                        "schema": "lw.node-local-checkpoint/1", "jobId": job["jobId"],
                        "leaseId": job["leaseId"], "checkpointSha256": checkpoint_hash,
                        "iteration": export_iteration,
                    }
                    origin_file = output / "checkpoint-origin.json"
                    origin_file.write_text(json.dumps(origin, sort_keys=True), encoding="utf-8")
                    export.update(
                        checkpoint=origin_file.name,
                        checkpointSha256=hashlib.sha256(origin_file.read_bytes()).hexdigest(),
                    )
                if finalize:
                    export["operation"] = "checkpoint_finalize"
                (output / "export.json").write_text(json.dumps(export), encoding="utf-8")
                if native is not None:
                    native.publish(output, export, versions)
                    if finalize:
                        checkpoint_runner.finalized_receipt(output / "observations/native-capture.json",
                                                            source["sha256"])
                if not finalize and getattr(runner, "env", None) is not None:
                    from lw_observation_capture import capture_observations

                    capture_observations(runner, output, inputs, export, versions)
                return result
            finally:
                if portable and not finalize:
                    runner.save = original_save
                runner._lw_in_bridged_learn = False

        learn._lw_bridged = True
        return learn

    _ACTIVE_WRAP_LEARN = _wrap_learn
    OnPolicyRunner.learn = _wrap_learn(OnPolicyRunner.learn)
    return None
