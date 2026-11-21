"""Apply the managed reference initialization to the pinned RSL-RL runner."""

import torch
from rsl_rl.modules import EmpiricalNormalization
from torch import nn

if __package__:
    from packages.training.reference_initialization import projection, receipt
else:
    from lw_reference_initialization import projection, receipt


@torch.no_grad()
def freeze_normalizers(runner, plan, *, restored):
    settings = plan["initialization"]["normalization"]
    mean = torch.tensor([settings["mean"]], dtype=torch.float32)
    variance = torch.tensor([settings["variance"]], dtype=torch.float32)
    buffers = {
        "_mean": mean, "_var": variance, "_std": variance.sqrt(),
        "count": torch.tensor(settings["count"], dtype=torch.long),
    }
    for model, group in ((runner.alg.actor, "actor"), (runner.alg.critic, "critic")):
        normalizer = model.obs_normalizer
        if (tuple(model.obs_groups) != (group,) or not isinstance(normalizer, EmpiricalNormalization)
                or normalizer.eps != 1e-2):
            raise ValueError("Managed initialization requires the reviewed actor and critic normalizers")
        for name, expected in buffers.items():
            actual = getattr(normalizer, name)
            if actual.shape != expected.shape or actual.dtype != expected.dtype:
                raise ValueError("Managed initialization normalizer tensor layout differs")
            if restored:
                if not torch.equal(actual.cpu(), expected):
                    raise ValueError("Checkpoint does not retain the approved frozen normalization")
            else:
                actual.copy_(expected)
        normalizer.until = settings["count"]
    if runner.alg.schedule != "fixed" or runner.alg.learning_rate != receipt(plan)["learningRate"]:
        raise ValueError("Runner PPO schedule or learning rate differs from the managed initialization")
    return receipt(plan)


@torch.no_grad()
def initialize_actor(runner, plan, task, spec):
    effective = freeze_normalizers(runner, plan, restored=False)
    actor = runner.alg.actor
    action = spec["action"]
    current, reference, offsets, scales = projection(spec, task)
    width = len(current)
    layers = list(actor.mlp)
    linear = layers[::2]
    expected = [actor.obs_dim, *plan["model"]["actor"], width]
    if (len(layers) != 5 or not all(isinstance(layer, nn.ELU) for layer in layers[1::2])
            or not all(isinstance(layer, nn.Linear) for layer in linear)
            or [layer.in_features for layer in linear] + [linear[-1].out_features] != expected):
        raise ValueError("Runner actor topology differs from the managed initialization")
    first, second, last = linear
    std = actor.distribution.std_param
    if (first.weight.dtype != torch.float32
            or not torch.equal(std, torch.full_like(std, effective["initialStd"]))):
        raise ValueError("Runner actor dtype or initial exploration differs from the managed initialization")

    weight = first.weight.new_zeros((width, actor.obs_dim))
    act_scale = weight.new_tensor(action["scale"])
    bias = -weight.new_tensor(action["defaultPosition"]) / act_scale
    rows = range(width)
    for columns, gain in ((current, -4), (reference, 5)):
        column_scales = [scales[index] for index in columns]
        term_scale = weight.new_tensor(column_scales)
        term_offset = weight.new_tensor([offsets[index] for index in columns])
        # Round the composed affine coefficient once at the float32 tensor boundary.
        coefficients = weight.new_tensor([
            gain / (action_scale * observation_scale)
            for action_scale, observation_scale in zip(action["scale"], column_scales, strict=True)
        ])
        weight[rows, columns] = coefficients
        bias += coefficients * term_offset * term_scale

    normalizer = actor.obs_normalizer
    shift = effective["shift"]
    first.weight[:width].copy_(weight * (normalizer.std + normalizer.eps))
    first.bias[:width].copy_(bias + weight @ normalizer.mean + shift)
    eye = torch.eye(width, dtype=weight.dtype, device=weight.device)
    second.weight[:width].zero_()
    second.weight[:width, :width].copy_(eye)
    second.bias[:width].zero_()
    last.weight.zero_()
    last.weight[:, :width].copy_(eye)
    last.bias.fill_(-shift)
    return effective
