"""Observe every frozen home first episode; the original oracle owns the verdict."""

import json
import os
from pathlib import Path

import torch


def install():
    import frozen_home_trace as base

    original = base.Trace

    class CompleteHomeTrace(original):
        def __init__(self, model, observer):
            super().__init__(model, observer)
            self.round2 = {
                "controls": torch.zeros(1024, device=str(self.device), dtype=torch.int32),
                "close": torch.zeros(1024, device=str(self.device), dtype=torch.int32),
                "open": torch.zeros(1024, device=str(self.device), dtype=torch.int32),
                "near_zero": torch.zeros(1024, device=str(self.device), dtype=torch.int32),
                "bilateral": torch.zeros(1024, device=str(self.device), dtype=torch.int32),
                "distance_min": torch.full((1024,), float("inf"), device=str(self.device)),
                "fixed_force_max": torch.zeros(1024, device=str(self.device)),
                "moving_force_max": torch.zeros(1024, device=str(self.device)),
                "first_terminal_body_q_xyzw": torch.zeros((1024, 6, 7), device=str(self.device)),
                "first_terminal_q_qd_target": torch.zeros((1024, 9, 3), device=str(self.device)),
            }
            self.extra_bytes = sum(value.numel() * value.element_size() for value in self.round2.values())
            if self.bytes + self.extra_bytes >= 64 * 1024**2:
                raise RuntimeError("Round2 diagnostic buffer cap exceeded")

        def manager_capture(self, env, manager, done):
            from isaaclab_newton.physics import NewtonManager

            live = base.tensor(self.first)[:, 0] == 0
            first_done = live & done
            raw_state = NewtonManager._state_0
            body = base.tensor(raw_state.body_q)[base.tensor(self.bodies).long()]
            pad_positions = []
            offset = body.new_tensor((0.0, 0.0, 0.04525)).expand(1024, 3)
            for index in (2, 3):
                quaternion = body[:, index, 3:]
                cross = torch.cross(quaternion[:, :3], offset, dim=-1)
                rotated = offset + 2 * (
                    quaternion[:, 3:] * cross + torch.cross(quaternion[:, :3], cross, dim=-1)
                )
                pad_positions.append(body[:, index, :3] + rotated)
            distance = torch.linalg.vector_norm(0.5 * sum(pad_positions) - body[:, 4, :3], dim=-1)
            grip = base.tensor(self.mean)[:, -1]
            self.round2["controls"] += live.int()
            self.round2["close"] += (live & (grip < -0.05)).int()
            self.round2["open"] += (live & (grip > 0.05)).int()
            self.round2["near_zero"] += (live & (grip.abs() <= 0.05)).int()
            self.round2["bilateral"] += (live & base.tensor(self.original_bilateral).bool()).int()
            self.round2["distance_min"][live] = torch.minimum(
                self.round2["distance_min"][live], distance[live]
            )
            self.round2["first_terminal_body_q_xyzw"][first_done] = body[first_done]
            maps = self.mapping
            q = base.tensor(raw_state.joint_q)[torch.as_tensor(maps["q_ids"], device=str(self.device)).long()]
            qd = base.tensor(raw_state.joint_qd)[
                torch.as_tensor(maps["v_ids"], device=str(self.device)).long()
            ]
            target = base.tensor(NewtonManager._control.joint_target_q)[
                torch.as_tensor(maps["target_q_ids"], device=str(self.device)).long()
            ]
            self.round2["first_terminal_q_qd_target"][first_done] = torch.stack((q, qd, target), -1)[
                first_done
            ]
            super().manager_capture(env, manager, done)
            values = base.tensor(self.manager)
            for name, column in (
                ("fixed_force_max", "fixed_force_norm_raw"),
                ("moving_force_max", "moving_force_norm_raw"),
            ):
                force = values[:, base.MANAGER_FIELDS.index(column)]
                self.round2[name][live] = torch.maximum(self.round2[name][live], force[live])

        def persist(self, *args, **kwargs):
            status = super().persist(*args, **kwargs)
            if status is not None:
                out = Path(os.environ["FR3_DIAG_EVIDENCE"])
                with (out / "round2-first-episode.pt").open("xb") as stream:
                    torch.save({name: value.detach().cpu() for name, value in self.round2.items()}, stream)
                with (out / "round2-observation.json").open("x", encoding="utf-8") as stream:
                    json.dump(
                        {
                            "scope": "Manager-sampled contact forces; "
                            "no claim about unobserved transient contacts",
                            "extra_bytes": self.extra_bytes,
                            "joint_fields": ["q", "qd", "target_q"],
                            "body_roles": base.BODY_ROLES,
                            "quaternion": "xyzw",
                            "acceptance_owner": "Original fr3_contact.evaluation; "
                            "diagnostic exit0 is not acceptance",
                        },
                        stream,
                        indent=2,
                    )
            return status

    base.Trace = CompleteHomeTrace
    return base.install()
