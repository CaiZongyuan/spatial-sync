"""Controlled program bytes carrying an observable import marker."""

import yaml

SOURCE = b"""import math
from packages.info.program import ProgramConfig

math._lw_uploaded_program_marker = True

class ScriptProgram:
    id, type, tick_priority = "robot_arm.script.probe", "robot-arm/v1", 10
    Config = ProgramConfig
    def init(self, ctx): pass
    def accept(self, inv, ctx): pass
    def start(self, inv, ctx): pass
    def tick(self, ctx, dt): pass
    def cancel(self, inv, ctx): pass
"""


def program_directory(tmp_path):
    package = tmp_path / "program"
    package.mkdir()
    manifest = {
        "schema": "lw.program-package/1",
        "name": "probe",
        "title": "Owned code snapshot",
        "entrypoint": "controller.py:ScriptProgram",
        "implements": "robot-arm/v1.pickPlace",
        "configSchema": {"type": "object", "properties": {}},
        "config": {},
    }
    (package / "program.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (package / "controller.py").write_bytes(SOURCE)
    return package, manifest


CLOSING_SOURCE = b"""from packages.info.program import ProgramConfig

class ScriptProgram:
    id, type, tick_priority = "robot_arm.script.gripper-probe", "robot-arm/v1", 10
    class Config(ProgramConfig):
        fingers: list[str]
    def init(self, ctx):
        self.fingers = [ctx.physics.bind_joint(key) for key in ctx.config.fingers]
        self.inv = None
    def accept(self, inv, ctx):
        return None
    def start(self, inv, ctx):
        self.inv = inv
    def tick(self, ctx, dt):
        if self.inv is None:
            return
        for joint in self.fingers:
            ctx.physics.set_joint_target(joint, [0.0])
        if sum(float(ctx.physics.joint_q(joint)[0]) for joint in self.fingers) < 0.02:
            self.inv.succeed()
            self.inv = None
    def cancel(self, inv, ctx):
        self.inv = None
        inv.cancelled()
"""


def closing_program_directory(path, fingers):
    path.mkdir(parents=True)
    manifest = {
        "schema": "lw.program-package/1",
        "name": "gripper-probe",
        "title": "Finite CPU gripper controller; tube transfer is evaluated independently",
        "entrypoint": "controller.py:ScriptProgram",
        "implements": "robot-arm/v1.pickPlace",
        "configSchema": {
            "type": "object",
            "properties": {"fingers": {"type": "array", "items": {"type": "string"}}},
            "required": ["fingers"],
        },
        "config": {"fingers": fingers},
    }
    (path / "program.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (path / "controller.py").write_bytes(CLOSING_SOURCE)
    return path
