"""Public HostCore seam; a deterministic external PhysicsPort records real stroke feedback."""

from pathlib import Path

import numpy as np
import yaml

from packages.info.catalog import load_catalog
from packages.info.program import HostCore, ProgramConfig
from packages.programs.cell_passage.workcell import WorkcellProgram
from packages.protocol.schema import SceneManifest


class StrokePort:
    def __init__(self):
        self.q = {key: 0.0 for key in ("x", "y", "z", "pump")}
        self.targets = self.q.copy()
        self.welds = set()
        self.velocities = self.q.copy()

    def bind_body(self, key):
        return key

    def weld(self, parent, child):
        self.welds.add((parent, child))

    def unweld(self, parent, child):
        self.welds.remove((parent, child))

    def bind_joint(self, key):
        return key

    def bind_site(self, key):
        return key

    def joint_q(self, key):
        return np.array([self.q[key]])

    def joint_qd(self, key):
        return np.array([self.velocities[key]])

    def set_joint_target(self, key, value):
        self.targets[key] = float(value[0])

    def set_joint_target_velocity(self, key, value):
        pass

    def site_pose(self, key):
        return np.zeros(3), np.array([0, 0, 0, 1])

    def advance(self, pump):
        self.q["pump"] = float(
            np.clip(
                pump, min(self.q["pump"], self.targets["pump"]), max(self.q["pump"], self.targets["pump"])
            )
        )


class FactsProgram:
    id, type, tick_priority = "facts.test", "facts-test/v1", 1

    class Config(ProgramConfig):
        station: str = "/imager"
        settled: bool = True
        completedCycle: str = "cycle-1"
        completedLoad: list[str] = ["/source"]
        cycleRunSeconds: float = 2
        actualRotorTurns: float = 1
        phase: str = "idle"

    def init(self, ctx):
        ctx.set(**ctx.config.model_dump())


def build_workcell_host(tmp_path, *, vessels=None, config=None, facts=None):
    bindings = []
    if facts is not None:
        bindings.append(
            {"key": "/peer", "type": FactsProgram.type, "program": FactsProgram.id, "config": facts}
        )
    scene = tmp_path / "devices.yaml"
    bindings.append(
        {
            "key": "/workcell",
            "type": WorkcellProgram.type,
            "program": WorkcellProgram.id,
            "config": {
                "axisJoints": ["x", "y", "z"],
                "pumpJoint": "pump",
                "tipSite": "tip",
                "pipetteBody": "/pipette",
                "vessels": vessels
                or [
                    {
                        "id": "source",
                        "entityKey": "/source",
                        "site": "/sourceSite",
                        "capacityUl": 1000,
                        "volumeUl": 500,
                        "reagent": "medium",
                    }
                ],
                "tips": [{"id": "T1", "site": "/T1", "bodyKey": "/tip1"}],
                **(config or {}),
            },
        }
    )
    scene.write_text(yaml.safe_dump({"scene": "test", "entities": bindings}), encoding="utf8")
    manifest = SceneManifest(
        sessionId="test",
        sceneRevision="r",
        revisionNumericId=1,
        sceneName="test",
        source={"kind": "synthetic", "physicsDt": 1 / 240},
        nodes=[{"kind": "group", "entityKey": row["key"], "name": row["key"]} for row in bindings],
        sites=[
            {"entityKey": name, "parentBodyKey": None, "localPose": {}} for name in ("/sourceSite", "/T1")
        ],
    )
    registry = {WorkcellProgram.id: WorkcellProgram, FactsProgram.id: FactsProgram}
    (tmp_path / "types").mkdir()
    definition = Path("devices/types/cell-passage-workcell.v1.yaml").read_text(encoding="utf8")
    (tmp_path / "types/workcell.yaml").write_text(definition, encoding="utf8")
    fields = FactsProgram.Config.model_fields
    properties = {
        name: {
            "schema": {"type": "array", "items": {"type": "string"}}
            if name == "completedLoad"
            else {
                "type": "boolean"
                if name == "settled"
                else "number"
                if name in ("cycleRunSeconds", "actualRotorTurns")
                else "string"
            }
        }
        for name in fields
    }
    (tmp_path / "types/facts.yaml").write_text(
        yaml.safe_dump(
            {"type": FactsProgram.type, "title": "External mechanical facts", "properties": properties}
        ),
        encoding="utf8",
    )
    port = StrokePort()
    host = HostCore(
        load_catalog(manifest, "test", registry, root=tmp_path, scene_file=scene), registry, physics=port
    )
    return host, port
