"""Build an author package for the public import API, without modifying shipped scenes."""

import io
import sys
import tarfile
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[2]
package = root / "library/scenes/lab-demo/2"
scene = yaml.safe_load((package / "scene.yaml").read_text(encoding="utf-8"))
scene["title"] = "Live UI acceptance laboratory"
bindings = yaml.safe_load((package / "devices.yaml").read_text(encoding="utf-8"))
incubator = next(entity for entity in bindings["entities"] if entity["type"] == "incubator/v1")
incubator["config"] = {"initialTemperatureC": 37, "setpointC": 37}
incubator["alerts"] = {
    "tempDeviation": {
        "when": {"all": [{"property": "temperatureC", "deviatesFrom": "setpointC", "by": 1}]},
        "forSeconds": 0.25,
        "severity": "critical",
        "message": "培养箱温度偏离设定值",
        "hint": "检查门是否关闭，再查看 lw history",
    }
}
files = {
    "scene.yaml": yaml.safe_dump(scene, allow_unicode=True).encode(),
    "devices.yaml": yaml.safe_dump(bindings, allow_unicode=True).encode(),
    "locations.yaml": (package / "locations.yaml").read_bytes(),
}
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
    for name, content in files.items():
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
