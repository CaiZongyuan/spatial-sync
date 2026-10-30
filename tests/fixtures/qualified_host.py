"""External deterministic feedback for Host handoff tests; not Newton evidence."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

from packages.info.catalog import load_catalog
from packages.info.program import HostCore, ProgramConfig
from packages.programs.labware.facts import LabwareFactsProgram
from packages.programs.labware.motion import LabwareMotionProgram
from packages.protocol.schema import SceneManifest
from tests.unit.test_qualified_placement import Port


class FeedbackPort(Port):
    def __init__(self):
        super().__init__()
        self.body = np.r_[self.body, np.tile([10, 0, 0, 0, 0, 0, 1], (9, 1))]
        self.velocity = np.zeros((13, 3))
        self.q = self.target = np.zeros(7)
        self.qd = np.zeros(7)
        self.width, self.width_target, self.width_velocity = 0.05, 0.05, 0.0
        self.relative = {}
        self.releasing = set()
        self.lingering = False
        self.last_time = 0.0
        self.advance(0.01)
        self.advance(0.01)
        self.last_time = 0.0

    def bind_joint(self, key):
        return SimpleNamespace(key=key, child=11 + int(key[1:]) if key.startswith("f") else 3)

    def joint_q(self, joint):
        return np.array([self.width / 2 if joint.key.startswith("f") else self.q[int(joint.key[1:])]])

    def joint_qd(self, joint):
        return np.array(
            [self.width_velocity / 2 if joint.key.startswith("f") else self.qd[int(joint.key[1:])]]
        )

    def set_joint_target(self, joint, values):
        if joint.key.startswith("f"):
            self.width_target = values[0] * 2
        else:
            self.target[int(joint.key[1:])] = values[0]

    def site_pose(self, site):
        site = site.key if hasattr(site, "key") else site
        pose = self.body[3] if site == "/tcp" else self.site_poses(self.bind_sites([site]))[0]
        return pose[:3], pose[3:]

    def weld(self, parent, body):
        if (parent, body) in self.enabled:
            return
        self.relative[parent, body] = self.body[body].copy()
        if parent >= 0:
            self.relative[parent, body][:3] -= self.body[parent, :3]
        super().weld(parent, body)

    def relative_site_velocity(self, body, sites):
        return np.array([self.velocity[body] - (self.velocity[s.body] if s.body >= 0 else 0) for s in sites])

    def collision_contacts(self, **kwargs):
        close = (
            self.width <= 0.0171
            and np.linalg.norm(self.body[3, :3] - self.body[0, :3] - [0, 0, 0.035]) < 0.001
        )
        if close or self.lingering and (-1, 0) in self.enabled and self.width >= 0.049:
            rows = [
                SimpleNamespace(
                    bodies=(finger, 0),
                    shapes=(f"/finger{finger - 11}/Pad", "/tube/Handle"),
                    distance_m=-0.0001,
                )
                for finger in (11, 12)
            ]
        else:
            rows = []
        return [
            r
            for r in rows
            if r.distance_m <= kwargs.get("max_distance", 0)
            and set(r.bodies) & kwargs.get("bodies", set(r.bodies))
        ]

    def advance(self, dt):
        self.last_time += dt
        before = self.body.copy()
        self.qd = (self.target - self.q) / dt
        self.q = self.target.copy()
        opening = self.width_target
        if np.linalg.norm(self.body[3, :3] - self.body[0, :3] - [0, 0, 0.035]) < 0.001:
            opening = max(opening, 0.017)  # External contact-limited synthetic jaw feedback.
        self.width_velocity = (opening - self.width) / dt
        self.width = opening
        self.body[3, :3] = [self.q[0], 0, self.q[1] + 0.035]
        for _ in range(2):
            for parent, child in sorted(self.enabled):
                pose = self.relative[parent, child].copy()
                if parent >= 0:
                    pose[:3] += self.body[parent, :3]
                self.body[child] = pose
        self.velocity = (self.body[:, :3] - before[:, :3]) / dt


class Ownership:
    id, type, tick_priority = "qualified.fixture", "qualified-fixture/v1", 1
    Config = ProgramConfig

    def init(self, ctx):
        ctx.set(
            controlOwner="auto",
            controlEpoch=0,
            controlMode="auto",
            materialSnapshot={
                "reservations": [
                    {"resource": k, "device": "/arm", "owner": "auto", "epoch": 0}
                    for k in ("/tube", "/cap", "/source", "/destination", "/tube/LidSeat")
                ],
            },
        )


def build_qualified_host(root):
    config = {
        "locations": {
            "locations": {
                name: {
                    "site": "/" + name,
                    "zone": name,
                    "requires": [],
                    "approach": [x, 0.04, 0, 0, 0, 0, 0],
                    "grasp": [x, 0, 0, 0, 0, 0, 0],
                }
                for name, x in (("source", 0.0), ("destination", 1.0))
            }
        },
        "tubes": ["/tube", "/cap"],
        "jointKeys": [f"j{i}" for i in range(7)],
        "fingerKeys": ["f0", "f1"],
        "handBody": "/hand",
        "tcpSite": "/tcp",
        "controlEntity": "/control",
        "graspOffsets": {"/tube": [0, 0, 0.035], "/cap": [0, 0, 0.015]},
        "gripDiameters": {"/tube": 0.017, "/cap": 0.017},
        "gripShapePrefixes": {"/tube": "/tube/Handle", "/cap": "/cap/Handle"},
        "fingerPadShapes": ["/finger0/Pad", "/finger1/Pad"],
        "attachedLids": {"/tube": "/cap"},
        "automaticOpenWidthM": 0.05,
        "gripperSpeedMPerS": 0.02,
        "qualifiedPlacement": True,
        "placementStations": {"/tube": ["/source", "/destination"], "/cap": ["/tube/LidSeat"]},
        "initialPlacements": {"/tube": "/source", "/cap": "/tube/LidSeat"},
        "expectedContactPairs": [[f"/finger{i}/Pad", "/tube/Handle"] for i in (0, 1)],
    }
    bindings = [
        {"key": "/control", "program": Ownership.id, "type": Ownership.type},
        {
            "key": "/arm",
            "program": LabwareMotionProgram.id,
            "type": LabwareMotionProgram.type,
            "config": config,
        },
        *[
            {
                "key": key,
                "program": LabwareFactsProgram.id,
                "type": LabwareFactsProgram.type,
                "config": {
                    "robots": ["/arm"],
                    "locationSites": sites,
                    **({"lidBody": "/cap", "lidOffsetM": 0.05} if key == "/tube" else {}),
                },
            }
            for key, sites in (("/tube", ["/source", "/destination"]), ("/cap", ["/tube/LidSeat"]))
        ],
    ]
    (root / "types").mkdir()
    for name in ("labware-motion", "labware"):
        (root / "types" / (name + ".yaml")).write_text(Path(f"devices/types/{name}.v1.yaml").read_text())
    props = {
        "controlOwner": {"schema": {"type": "string"}},
        "controlEpoch": {"schema": {"type": "integer"}},
        "controlMode": {"schema": {"type": "string"}},
        "materialSnapshot": {
            "schema": {
                "type": "object",
                "properties": {
                    "reservations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                k: {"type": "integer" if k == "epoch" else "string"}
                                for k in ("resource", "device", "owner", "epoch")
                            },
                        },
                    }
                },
            }
        },
    }
    (root / "types/control.yaml").write_text(
        yaml.safe_dump({"type": Ownership.type, "title": "Fixture", "properties": props})
    )
    scene = root / "devices.yaml"
    scene.write_text(yaml.safe_dump({"scene": "qualified", "entities": bindings}))
    manifest = SceneManifest(
        sessionId="qualified",
        sceneRevision="q",
        revisionNumericId=1,
        sceneName="fixture",
        source={"kind": "synthetic", "physicsDt": 0.01},
        sites=[
            {"entityKey": key, "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}}
            for key in ("/source", "/destination", "/tube/LidSeat", "/tcp")
        ],
        nodes=[{"kind": "group", "entityKey": r["key"], "name": r["key"]} for r in bindings],
    )
    registry = {p.id: p for p in (Ownership, LabwareMotionProgram, LabwareFactsProgram)}
    port = FeedbackPort()
    return HostCore(
        load_catalog(manifest, "qualified", registry, root=root, scene_file=scene), registry, physics=port
    ), port
