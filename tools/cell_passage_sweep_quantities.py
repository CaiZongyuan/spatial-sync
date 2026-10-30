"""Resolve L0 pump endpoints through the unchanged runtime quantity resolver.

This is an ideal-command projection for geometry, not a material audit. Contact
and mating checks belong to the complete native geometry adapter, never this
numeric context.
"""

from types import SimpleNamespace

import numpy as np

from packages.info.program import Reject
from packages.programs.cell_passage.workcell import (
    WorkcellProgram,
    base_action,
    empty_content,
    move_content,
)
from tools.cell_passage_plan import WORKCELL


class _NumericPort:
    last_time = 0.0

    def __init__(self):
        self.position = 0.0
        self.limits = (0.0, 0.01)

    @staticmethod
    def bind_joint(key):
        return key

    bind_body = bind_joint
    bind_site = bind_joint

    @staticmethod
    def bind_sites(keys):
        return tuple(keys)

    def joint_q(self, key):
        return np.array([self.position if key.endswith("FlexPump") else 0.0])

    @staticmethod
    def joint_qd(key):
        return np.zeros(1)

    def joint_limits(self, key):
        return np.array([self.limits[0]]), np.array([self.limits[1]])

    @staticmethod
    def collision_contacts(**kwargs):
        raise ValueError("Numeric projection cannot supply contact facts")


class QuantityProjection:
    def __init__(self, config, read):
        self.program = WorkcellProgram()
        self.port = _NumericPort()
        self.values = {}
        self.external_read = read
        self.context = SimpleNamespace(
            key=WORKCELL,
            config=WorkcellProgram.Config.model_validate(config),
            physics=self.port,
            sim_time=0.0,
            tick_index=0,
            set=lambda **v: self.values.update(v),
            get=lambda name: self.values.get(name),
            read=self._read,
            emit=lambda *a, **kw: None,
        )
        self.program.init(self.context)
        self.program._finish = lambda *args: None

    def _read(self, entity, prop):
        if entity == WORKCELL:
            if prop == "materialSnapshot":
                return self.program.state
            return self.values.get(prop)
        return self.external_read(entity, prop)

    def observe(self, action, params):
        self.values.update(
            controlOwner=params.get("owner"), controlEpoch=params.get("epoch"), controlMode="auto"
        )
        if action in ("reserve", "release", "detach", "pellet"):
            self.program.handle = SimpleNamespace(action=action, params=params)
            self.program._immediate(self.context)
        elif action == "pickupTip":
            self.program.state["tip"] = empty_content() | {
                "id": params["tip"],
                "contactCulture": "",
                "contactWaste": False,
            }
        elif action == "discardTip":
            tip = self.program.state["tip"]
            move_content(tip, self.program._vessel(params["waste"]), tip["volumeUl"])
            self.program.state["tip"] = empty_content() | {
                "id": "",
                "contactCulture": "",
                "contactWaste": False,
            }

    def stroke(self, action, params, current, limits):
        self.port.position, self.port.limits = current, limits
        self.values.update(controlOwner=params["owner"], controlEpoch=params["epoch"], controlMode="auto")
        vessel = self.program._vessel(params["vessel"])
        plan = self.program._quantity_plan(
            SimpleNamespace(action=action, params=params), self.context, vessel
        )
        if isinstance(plan, Reject):
            raise ValueError("Runtime quantity resolver: " + plan.code)
        target = float(np.float32(plan["pumpTargetM"]))
        kind = base_action(action)
        amount = plan["resolvedUl"]
        tip = self.program.state["tip"]
        if kind == "aspirate":
            move_content(vessel, tip, amount)
            self.program._mark_tip_contact(vessel)
        elif kind == "dispense":
            move_content(tip, vessel, amount)
            self.program._mark_tip_contact(vessel)
        # Mix is a closed geometric out-and-back. No net liquid displacement.
        return plan, [current, target, current] if kind == "mix" else [current, target]
