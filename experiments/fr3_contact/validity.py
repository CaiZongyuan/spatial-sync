"""One raw episode-invalid fact shared by termination, rewards, reset and audit."""

import torch

RANGE, VELOCITY, TUBE_LOST, NONFINITE = 1, 2, 4, 8
REASONS = {"range": RANGE, "velocity": VELOCITY, "tube_lost": TUBE_LOST, "nonfinite": NONFINITE}


class EpisodeValidity:
    def __init__(self, num_envs, device):
        self.reasons = torch.zeros(num_envs, dtype=torch.int32, device=device)
        self.first_sample = torch.full_like(self.reasons, -1)
        self.samples = torch.zeros_like(self.reasons)
        self.outputs = torch.zeros_like(self.reasons)
        self.transition_outputs = torch.zeros_like(self.reasons)
        self.max_excess = torch.zeros(num_envs, device=device)
        self.max_speed = torch.zeros_like(self.max_excess)
        self.lifted = torch.zeros(num_envs, dtype=torch.int32, device=device)

    @property
    def invalid(self):
        return self.reasons != 0

    def mark(self, reasons, excess=None, speed=None):
        first = (self.reasons == 0) & (reasons != 0)
        self.first_sample[first] = self.samples[first]
        self.reasons.bitwise_or_(reasons.to(torch.int32))
        if excess is not None:
            self.max_excess.copy_(torch.maximum(self.max_excess, excess))
        if speed is not None:
            self.max_speed.copy_(torch.maximum(self.max_speed, speed))

    def observe_joints(self, position, velocity, limits, *, output=False):
        """CPU/manager seam; native physics uses the same rule on unmodified raw arrays."""
        finite = torch.isfinite(position).all(-1) & torch.isfinite(velocity).all(-1)
        excess = torch.maximum(limits[..., 0] - position, position - limits[..., 1]).clamp_min(0)
        excess = torch.nan_to_num(excess, nan=0.0, posinf=0.0, neginf=0.0).amax(-1)
        speed = torch.nan_to_num(velocity.abs(), nan=0.0, posinf=0.0, neginf=0.0).amax(-1)
        reasons = (excess > 0).int() * RANGE | (speed > 12).int() * VELOCITY | (~finite).int() * NONFINITE
        self.mark(reasons, excess, speed)
        self.samples += 1
        if output:
            self.outputs += 1
            self.transition_outputs += 1

    def snapshot(self):
        return {
            name: getattr(self, name).clone()
            for name in (
                "reasons",
                "first_sample",
                "samples",
                "outputs",
                "max_excess",
                "max_speed",
                "transition_outputs",
            )
        }

    def reset(self, ids=None):
        ids = slice(None) if ids is None else ids
        for name in ("reasons", "samples", "outputs", "max_excess", "max_speed", "lifted"):
            getattr(self, name)[ids] = 0
        self.first_sample[ids] = -1


def episode_validity(env):
    if not hasattr(env, "_fr3_validity"):
        env._fr3_validity = EpisodeValidity(env.num_envs, env.device)
    return env._fr3_validity


def failure_dominates_timeout(env, timeout):
    return timeout & ~episode_validity(env).invalid
