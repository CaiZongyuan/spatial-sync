"""Checkpoint adapter for the pinned managed mjlab / RSL-RL PPO runner."""

import json
import os
import struct
import sys
import tempfile
from pathlib import Path

import torch

if __package__:
    from packages.training import checkpoint_format as wire
else:
    import lw_checkpoint_format as wire

DTYPES = dict(
    zip(
        wire.DTYPE_BYTES,
        (
            torch.bool,
            torch.uint8,
            torch.int8,
            torch.int16,
            torch.int32,
            torch.int64,
            torch.float16,
            torch.bfloat16,
            torch.float32,
            torch.float64,
        ),
        strict=True,
    )
)


def capture(runner, identity, iteration):
    if sys.byteorder != "little":
        raise RuntimeError("Portable checkpoints require a little-endian trainer")
    header, buffers = {}, bytearray()
    names = {dtype: name for name, dtype in DTYPES.items()}

    def encode(value):
        if isinstance(value, torch.Tensor):
            tensor = value.detach().to(device="cpu").contiguous()
            if tensor.is_floating_point() and not torch.isfinite(tensor).all().item():
                raise ValueError("Cannot save a non-finite PPO tensor")
            name = f"t{len(header)}"
            size = tensor.numel() * tensor.element_size()
            if len(buffers) + size > wire.MAX_BYTES or len(header) >= wire.MAX_TENSORS:
                raise ValueError("Checkpoint tensor payload is too large")
            header[name] = {
                "dtype": names[tensor.dtype],
                "shape": list(tensor.shape),
                "data_offsets": [len(buffers), len(buffers) + size],
            }
            buffers.extend(tensor.reshape(-1).view(torch.uint8).numpy().tobytes())
            return {"tensor": name}
        if isinstance(value, dict):
            return {str(key): encode(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [encode(child) for child in value]
        if value is None or type(value) in (int, float, bool, str):
            return value
        raise ValueError("Unsupported PPO state value")

    state = encode(runner.alg.save())
    encoded = wire.json_bytes(header)
    encoded += b" " * (-len(encoded) % 8)
    tensors = struct.pack("<Q", len(encoded)) + encoded + buffers
    metadata = identity | {
        "iteration": iteration,
        "environmentSteps": int(runner.env.unwrapped.common_step_counter),
        "learningRate": runner.alg.learning_rate,
        "tensorSha256": wire.digest(tensors),
        "state": state,
    }
    return wire.pack_checkpoint(metadata, tensors)


def _model_state(model, received):
    expected = model.state_dict()
    if received.keys() != expected.keys() or any(
        not isinstance(received[key], torch.Tensor)
        or received[key].shape != tensor.shape
        or received[key].dtype != tensor.dtype
        for key, tensor in expected.items()
    ):
        raise ValueError("Checkpoint model state signature does not match the target runner")


def _optimizer_tensor_keys(optimizer, group):
    if isinstance(optimizer, (torch.optim.Adam, torch.optim.AdamW)):
        keys = {"step", "exp_avg", "exp_avg_sq"}
        if group["amsgrad"]:
            keys.add("max_exp_avg_sq")
        return keys
    if isinstance(optimizer, torch.optim.RMSprop):
        keys = {"step", "square_avg"}
        if group["momentum"]:
            keys.add("momentum_buffer")
        if group["centered"]:
            keys.add("grad_avg")
        return keys
    if isinstance(optimizer, torch.optim.SGD):
        return {"momentum_buffer"} if group["momentum"] else set()
    raise ValueError("Unsupported PPO optimizer for portable checkpoint")


def _optimizer_state(optimizer, received, *, learning_rate=None):
    if not isinstance(received, dict) or set(received) != {"state", "param_groups"}:
        raise ValueError("Checkpoint optimizer shape changed")
    expected = optimizer.state_dict()["param_groups"]
    groups = received["param_groups"]
    if not isinstance(groups, list) or len(groups) != len(expected):
        raise ValueError("Checkpoint optimizer parameter groups changed")
    tensors = {}
    for group, template, actual in zip(groups, expected, optimizer.param_groups, strict=True):
        if not isinstance(group, dict) or group.keys() != template.keys():
            raise ValueError("Checkpoint optimizer options changed")
        for key, value in template.items():
            if isinstance(value, tuple):
                value = list(value)
            if key == "lr" and learning_rate is not None:
                value = learning_rate
            if group[key] != value or type(group[key]) is not type(value):
                raise ValueError(f"Checkpoint optimizer option changed: {key}")
        keys = _optimizer_tensor_keys(optimizer, group)
        for number, tensor in zip(group["params"], actual["params"], strict=True):
            tensors[str(number)] = (tensor, keys)
    state = received["state"]
    if not isinstance(state, dict) or not state.keys() <= tensors.keys():
        raise ValueError("Checkpoint optimizer parameter identifiers changed")
    restored = {}
    for number, values in state.items():
        parameter, keys = tensors[number]
        if not isinstance(values, dict) or values.keys() != keys:
            raise ValueError("Checkpoint optimizer state keys changed")
        for key, tensor in values.items():
            if not isinstance(tensor, torch.Tensor):
                raise ValueError("Checkpoint optimizer state requires tensors")
            if key == "step":
                if tensor.shape or tensor.dtype not in (torch.float32, torch.float64) or tensor.item() < 0:
                    raise ValueError("Invalid checkpoint optimizer step")
            elif tensor.shape != parameter.shape or tensor.dtype != parameter.dtype:
                raise ValueError("Checkpoint optimizer tensor does not match its parameter")
        restored[int(number)] = values
    received["state"] = restored


def restore(runner, raw, descriptor):
    if sys.byteorder != "little":
        raise RuntimeError("Portable checkpoints require a little-endian trainer")
    if len(raw) != descriptor["size"] or wire.digest(raw) != descriptor["sha256"]:
        raise ValueError("Checkpoint bytes differ from the leased input")
    metadata, tensors, header, buffers = wire.read_checkpoint(raw)
    for key in ("jobId", "leaseId", "inputsSha256", "runtime", "iteration", "tensorSha256"):
        if metadata[key] != descriptor[key]:
            raise ValueError("Checkpoint metadata differs from the leased input")

    def decode(value):
        if isinstance(value, dict):
            if set(value) == {"tensor"}:
                spec = header[value["tensor"]]
                start, end = spec["data_offsets"]
                dtype, shape = DTYPES[spec["dtype"]], spec["shape"]
                if start == end:
                    return torch.empty(shape, dtype=dtype)
                tensor = torch.frombuffer(bytearray(buffers[start:end]), dtype=dtype).reshape(shape)
                if tensor.is_floating_point() and not torch.isfinite(tensor).all().item():
                    raise ValueError("Non-finite checkpoint tensor")
                return tensor
            return {key: decode(child) for key, child in value.items()}
        return [decode(child) for child in value] if isinstance(value, list) else value

    state = decode(metadata["state"])
    algorithm = runner.alg
    if bool(algorithm.rnd) != ("rnd_state_dict" in state):
        raise ValueError("Checkpoint RND does not match the target runner")
    _model_state(algorithm.actor, state["actor_state_dict"])
    _model_state(algorithm.critic, state["critic_state_dict"])
    _optimizer_state(
        algorithm.optimizer, state["optimizer_state_dict"], learning_rate=metadata["learningRate"]
    )
    if algorithm.rnd:
        _model_state(algorithm.rnd, state["rnd_state_dict"])
        _optimizer_state(algorithm.rnd_optimizer, state["rnd_optimizer_state_dict"])
    algorithm.load(state, None, strict=True)
    algorithm.learning_rate = metadata["learningRate"]
    runner.env.unwrapped.common_step_counter = metadata["environmentSteps"]
    runner.current_learning_iteration = metadata["iteration"]
    return metadata, tensors


def publish(directory, runner, identity, iteration):
    directory.mkdir(exist_ok=True)
    raw = capture(runner, identity, iteration)
    destination = directory / f"{iteration:016d}.tar"
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    # A long outage can leave training ahead of the upload loop. The latest
    # complete save is sufficient for continuation and bounds the local spool.
    for old in sorted(directory.glob("*.tar"))[:-2]:
        old.unlink(missing_ok=True)
    return raw


def collect_without_updates(runner, capture):
    if capture is None:
        raise ValueError("Checkpoint finalization requires a reviewed native observation profile")
    runner.alg.eval_mode()
    with torch.inference_mode():
        observations = runner.env.get_observations().to(runner.device)
        for _ in range(1024):
            if capture.error or len(capture.samples) == 256:
                break
            actions = runner.alg.act(observations)
            observations = runner.env.step(actions.to(runner.env.device))[0].to(runner.device)
    if capture.error or len(capture.samples) != 256:
        raise ValueError(capture.error or "Checkpoint finalization captured fewer than 256 native states")


def finalized_receipt(path, source_sha256):
    value = json.loads(path.read_bytes())
    value.update(
        schema="lw.policy-native-observation-capture/2",
        afterLearn=False,
        finalizedCheckpointSha256=source_sha256,
    )
    path.write_bytes(wire.json_bytes(value))
