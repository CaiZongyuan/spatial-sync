"""Ordered episode facts for the FR3 contact-grasp experiment."""

from collections.abc import Sequence

import torch


class ContactGraspProgress:
    """Require grasp, held clearance, insertion, then ten released resting samples.

    Curriculum resets may restore physically demonstrated grasp/lift history. The audit
    admits only home resets with empty history. Raw placement is retained as a diagnostic.
    """

    def __init__(self, num_envs: int, device: str, stable_steps: int = 10, grasp_steps: int = 2):
        self.stable_steps = stable_steps
        self.grasp_steps = grasp_steps
        self.grasped = torch.zeros(num_envs, dtype=torch.bool, device=device)
        self.lifted = torch.zeros_like(self.grasped)
        self.inserted = torch.zeros_like(self.grasped)
        self.success = torch.zeros_like(self.grasped)
        self.placement_success = torch.zeros_like(self.grasped)
        self.unsafe_rack_contact = torch.zeros_like(self.grasped)
        self.grasp_count = torch.zeros(num_envs, dtype=torch.long, device=device)
        self.stable_count = torch.zeros_like(self.grasp_count)
        self.placement_count = torch.zeros_like(self.grasp_count)
        self.time_to_success = torch.full_like(self.grasp_count, -1)

    def reset(self, env_ids: Sequence[int] | torch.Tensor | slice | None = None) -> None:
        """Clear only the requested episodes."""
        ids = slice(None) if env_ids is None else env_ids
        for buffer in (
            self.grasped,
            self.lifted,
            self.inserted,
            self.success,
            self.placement_success,
            self.unsafe_rack_contact,
        ):
            buffer[ids] = False
        for buffer in (self.grasp_count, self.stable_count, self.placement_count):
            buffer[ids] = 0
        self.time_to_success[ids] = -1

    def update(self, holding, cleared, inserted, seated, step, valid=None) -> torch.Tensor:
        """Latch each stage only after its predecessor was observed on an earlier step."""
        valid = torch.ones_like(holding) if valid is None else valid
        holding, cleared, inserted = holding & valid, cleared & valid, inserted & valid
        had_grasp = self.grasped.clone()
        had_lift = self.lifted.clone()
        had_insert = self.inserted.clone()
        self.grasp_count.copy_(torch.where(holding, self.grasp_count + 1, 0))
        self.grasped |= self.grasp_count >= self.grasp_steps
        self.lifted |= had_grasp & holding & cleared
        self.inserted |= had_lift & inserted
        completed_path = had_grasp & had_lift & had_insert
        self.stable_count.copy_(torch.where(completed_path & seated & valid, self.stable_count + 1, 0))
        self.placement_count.copy_(torch.where(seated, self.placement_count + 1, 0))
        self.placement_success |= self.placement_count >= self.stable_steps
        newly_successful = ~self.success & (self.stable_count >= self.stable_steps)
        self.time_to_success.copy_(torch.where(newly_successful, step, self.time_to_success))
        self.success.copy_((self.success | newly_successful) & valid)
        return self.success.clone()
