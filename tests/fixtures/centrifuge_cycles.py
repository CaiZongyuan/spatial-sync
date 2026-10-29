"""Measured-angle actuator fixture for real centrifuge programs; never Newton proof."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from packages.info.catalog import load_catalog
from packages.info.program import HostCore, ProgramConfig
from packages.programs.centrifuge.newton import CentrifugeProgram
from packages.programs.labware.centrifuge import PassageCentrifugeProgram
from packages.protocol.schema import SceneManifest

POSITIONS = np.array([[.15, 0, .02], [0, .15, .02], [-.15, 0, .02], [0, -.15, .02]])
TUBES = [f"/tube{i}" for i in range(8)]
SITES = [f"/slot{i}" for i in range(4)]
ARMS = [f"/arm{i}" for i in range(11)]


class RotorPort:
    def __init__(self):
        self.keys = ["/rotor", *TUBES, "/housing", *ARMS]
        self.body = np.array([[10, 0, 0, 0, 0, 0, 1]] * len(self.keys), dtype=float)
        self.body[0, :3] = self.body[9, :3] = 0
        self.q, self.qd, self.velocity = 0.0, 0.0, 0.0
        self.frozen = False
        self.welded = set()
        self.set_batch(0)

    def set_batch(self, batch):
        self.locations = {}
        for i, key in enumerate(TUBES):
            active = i // 4 == batch
            self.body[i + 1, :3] = POSITIONS[i % 4] if active else [2 + i, 0, .02]
            self.locations[key] = SITES[i % 4] if active else "rack"

    def bind_body(self, key):
        return self.keys.index(key)

    def bind_joint(self, key):
        return SimpleNamespace(child=0, key=key)

    def bind_sites(self, keys):
        return tuple(SimpleNamespace(body=0, index=SITES.index(key)) for key in keys)

    def collision_bounds(self, body):
        return np.array([[-.19, -.19, 0], [.19, .19, .05]] if body in (0, 9) else
                        [[-.009, -.009, 0], [.009, .009, .13]])

    def site_poses(self, sites):
        return np.column_stack((POSITIONS, np.tile([0, 0, 0, 1], (4, 1))))

    def relative_site_velocity(self, body, sites):
        return np.zeros((4, 3))

    def joint_q(self, joint):
        return np.array([self.q if joint.key == "spindle" else 0.0])

    def joint_qd(self, joint):
        return np.array([self.qd if joint.key == "spindle" else 0.0])

    def set_joint_target_velocity(self, joint, value):
        self.velocity = float(value[0])

    def weld(self, parent, body):
        self.welded.add((parent, body))

    def unweld(self, parent, body):
        self.welded.discard((parent, body))

    def advance(self, dt):
        self.qd = 0.0 if self.frozen else self.velocity
        self.q += self.qd * dt


class ControlFacts:
    id, type, tick_priority = "cycle.control.fixture", "cycle-control-fixture/v1", 1
    Config = ProgramConfig

    def init(self, ctx):
        self.owner, self.epoch = "auto", 0
        self.publish(ctx)

    def publish(self, ctx):
        ctx.set(controlOwner=self.owner, controlEpoch=self.epoch, controlMode="auto", materialSnapshot={
            "reservations": [{"resource": key, "device": "/cent", "owner": self.owner, "epoch": self.epoch}
                             for key in ["/cent", *TUBES]],
            "vessels": [{"entityKey": key, "volumeUl": 0} for key in TUBES],
        })

    def start(self, inv, ctx):
        if inv.action == "load":
            ctx.physics.set_batch(inv.params["batch"])
        else:
            self.owner, self.epoch = inv.params["owner"], self.epoch + 1
            self.publish(ctx)
        inv.succeed()


class TubeFacts:
    id, type, tick_priority = "cycle.tube.fixture", "cycle-tube-fixture/v1", 2
    Config = ProgramConfig

    def init(self, ctx):
        self.tick(ctx, 0)

    def tick(self, ctx, dt):
        ctx.set(location=ctx.physics.locations[ctx.key], lidClosed=True)


def build_cycle_host(root, *, legacy=False):
    root.mkdir(parents=True, exist_ok=True)
    program = CentrifugeProgram if legacy else PassageCentrifugeProgram
    registry = {p.id: p for p in (program, ControlFacts, TubeFacts)}
    config = {"slotSites": SITES, "tubes": TUBES, "spindleJoint": "spindle", "lidJoint": "lid"}
    if not legacy:
        config.update(controlEntity="/control", armBodies=ARMS, housingBody="/housing",
                      tareKg={key: .01 for key in TUBES})
    bindings = [{"key": "/control", "program": ControlFacts.id, "type": ControlFacts.type, "config": {}},
                *[{"key": key, "program": TubeFacts.id, "type": TubeFacts.type, "config": {}}
                  for key in TUBES],
                {"key": "/cent", "program": program.id, "type": program.type, "config": config}]
    scene = root / "devices.yaml"
    scene.write_text(yaml.safe_dump({"scene": "cycles", "entities": bindings}))
    types = root / "types"
    types.mkdir()
    name = "centrifuge.v1.yaml" if legacy else "passage-centrifuge.v1.yaml"
    (types / "cent.yaml").write_text(Path("devices/types", name).read_text())
    for name, definition in {
        "control": {"type": ControlFacts.type, "title": "Fixture controls", "properties": {
            "controlOwner": {"schema": {"type": "string"}}, "controlEpoch": {"schema": {"type": "integer"}},
            "controlMode": {"schema": {"type": "string"}}, "materialSnapshot": {"schema": {
                "type": "object", "properties": {
                    "reservations": {"type": "array", "items": {"type": "object", "properties": {
                        **{key: {"type": "string"} for key in ("resource", "device", "owner")},
                        "epoch": {"type": "integer"},
                    }}},
                    "vessels": {"type": "array", "items": {"type": "object", "properties": {
                        "entityKey": {"type": "string"}, "volumeUl": {"type": "number"},
                    }}},
                },
            }},
        }, "actions": {"load": {"params": {"batch": {"type": "integer", "enum": [0, 1]}},
                               "required": ["batch"]},
                       "handoff": {"params": {"owner": {"type": "string"}}, "required": ["owner"]}}},
        "tube": {"type": TubeFacts.type, "title": "Fixture tube", "properties": {
            "location": {"schema": {"type": "string"}}, "lidClosed": {"schema": {"type": "boolean"}},
        }},
    }.items():
        (types / f"{name}.yaml").write_text(yaml.safe_dump(definition))
    manifest = SceneManifest(sessionId="fixture", sceneRevision="cycles", revisionNumericId=1,
                             sceneName="Measured rotor fixture",
                             source={"kind": "synthetic", "physicsDt": .01},
                             nodes=[{"kind": "group", "entityKey": b["key"], "name": b["key"]}
                                    for b in bindings])
    port = RotorPort()
    host = HostCore(load_catalog(manifest, "cycles", registry, root=root, scene_file=scene),
                    registry, physics=port)
    return host, port, manifest


def advance_until_terminal(host, port, identity, *, count=5000):
    for _ in range(count):
        port.advance(.1)
        host.tick(host.sim_time + .1, .1)
        invocation = next(row for row in host.snapshot().invocations if row.invocationId == identity)
        if invocation.status in ("succeeded", "failed", "cancelled"):
            return invocation
    raise AssertionError("Fixture rotor did not finish")
