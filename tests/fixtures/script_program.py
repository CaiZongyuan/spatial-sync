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
