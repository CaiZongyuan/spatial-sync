"""Host boundary with external arm facts and explicit physical feedback."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from packages.info.catalog import load_catalog
from packages.info.program import HostCore, ProgramConfig
from packages.programs.labware.joints import PassageJointProgram
from packages.protocol.schema import SceneManifest


class ControlFacts:
    id, type, tick_priority = "guard.control", "guard-control/v1", 5

    class Config(ProgramConfig):
        pass

    def init(self, ctx):
        ctx.set(controlOwner="auto", controlEpoch=0, controlMode="auto",
                materialSnapshot={"reservations": [{"resource": "/joint", "device": "/joint",
                                                     "owner": "auto", "epoch": 0}]})

    def start(self, inv, ctx):
        ctx.set(controlEpoch=inv.params["epoch"])
        inv.succeed()


class ArmFacts:
    id, type, tick_priority = "guard.arm", "guard-arm/v1", 0

    class Config(ProgramConfig):
        holding: str | None = None
        lidClosed: bool = False

    def init(self, ctx):
        ctx.set(holding=ctx.config.holding, lidClosed=ctx.config.lidClosed,
                opened=False, closed=True, phase="idle", controlSafe=True)

    def start(self, inv, ctx):
        ctx.set(**inv.params)
        inv.succeed()


class JointPort:
    def __init__(self, door):
        self.q, self.qd = (0.8 if door else 0.0), 0.0
        self.target = self.q
        self.child = "/door" if door else "/rail"
        self.contacts, self.contact_error = [], False
        self.positions = {key: np.array([2.0, 0, 1]) for key in ("/armbody", "/cap", "/lid")}
        self.positions.update({"/anchor": np.zeros(3), "/door": np.array([0, 0, 1]), "/rail": np.zeros(3)})
        self.bounds_error = False
        self.welds = {("/armbody", "/cap")}

    def bind_joint(self, _):
        return SimpleNamespace(child=self.child)

    def bind_body(self, key):
        if key not in self.positions:
            raise KeyError(key)
        return key

    def joint_q(self, _):
        return np.array([self.q])

    def joint_qd(self, _):
        return np.array([self.qd])

    def set_joint_target(self, _, values):
        self.target = float(values[0])

    def set_joint_target_velocity(self, *_):
        pass

    def collision_contacts(self, *, max_distance=0):
        if self.contact_error:
            raise ValueError("Unknown contact backend")
        return [row for row in self.contacts if row.distance_m <= max_distance]

    def collision_bounds(self, body):
        if self.bounds_error:
            raise ValueError("Unknown collider bounds")
        return np.array([[-0.1] * 3, [0.1] * 3])

    def body_pose(self, body):
        value = self.positions[body]
        if value is None:
            raise ValueError("Unknown rigid pose")
        return value, np.array([0, 0, 0, 1])


def build_joint_host(tmp_path, *, door=False, holding="/cap", lid_closed=False, bounds_error=False):
    port = JointPort(door)
    port.bounds_error = bounds_error
    config = {"controlEntity": "/control", "jointKey": "/hinge", "lower": 0, "upper": 1,
              "maxSpeed": 0.1, "role": "inner" if door else "axis", "peerDoor": "/armbody",
              "armEntity": "/armbody", "movingBodyKeys": [port.child],
              "attachedLids": {"/cap": "/lid"}}
    if door:
        config.update(clearanceBodyKeys=["/armbody"], sweepAnchorBody="/anchor",
                      sweepLowerLocalM=[-0.5, -0.5, 0], sweepUpperLocalM=[0.5, 0.5, 2])
    bindings = [
        {"key": "/control", "type": ControlFacts.type, "program": ControlFacts.id},
        {"key": "/armbody", "type": ArmFacts.type, "program": ArmFacts.id, "config": {"holding": holding}},
        {"key": "/cap", "type": ArmFacts.type, "program": ArmFacts.id, "config": {"lidClosed": lid_closed}},
        {"key": "/joint", "type": PassageJointProgram.type, "program": PassageJointProgram.id,
         "config": config},
    ]
    scene = tmp_path / "devices.yaml"
    scene.write_text(yaml.safe_dump({"scene": "test", "entities": bindings}))
    types = tmp_path / "types"
    types.mkdir()
    (types / "joint.yaml").write_text(Path("devices/types/passage-joint.v1.yaml").read_text())
    for name, definition in {
        "control": {"type": ControlFacts.type, "properties": {
            "controlOwner": {"schema": {"type": "string"}},
            "controlEpoch": {"schema": {"type": "integer"}},
            "controlMode": {"schema": {"type": "string"}},
            "materialSnapshot": {"schema": {"type": "object", "properties": {
                "reservations": {"type": "array", "items": {"type": "object", "properties": {
                    "resource": {"type": "string"}, "device": {"type": "string"},
                    "owner": {"type": "string"}, "epoch": {"type": "integer"}},
                    "required": ["resource", "device", "owner", "epoch"]}}},
                "required": ["reservations"]}}},
            "actions": {"setEpoch": {"params": {"epoch": {"type": "integer"}}, "required": ["epoch"]}}},
        "arm": {"type": ArmFacts.type, "properties": {
            "holding": {"schema": {"type": ["string", "null"]}},
            "lidClosed": {"schema": {"type": "boolean"}},
            "opened": {"schema": {"type": "boolean"}}, "closed": {"schema": {"type": "boolean"}},
            "phase": {"schema": {"type": "string"}}, "controlSafe": {"schema": {"type": "boolean"}}},
            "actions": {"report": {"params": {"holding": {"type": ["string", "null"]},
                                                "lidClosed": {"type": "boolean"}}}}},
    }.items():
        (types / (name + ".yaml")).write_text(yaml.safe_dump(definition | {"title": name}))
    manifest = SceneManifest(sessionId="test", sceneRevision="r", revisionNumericId=1, sceneName="test",
                             source={"kind": "synthetic", "physicsDt": 1 / 240},
                             nodes=[{"kind": "group", "entityKey": row["key"], "name": row["key"]}
                                    for row in bindings])
    registry = {program.id: program for program in (ControlFacts, ArmFacts, PassageJointProgram)}
    return HostCore(load_catalog(manifest, "test", registry, root=tmp_path, scene_file=scene),
                    registry, physics=port), port

