"""Real small scene packages shared by gateway version and recording contracts."""

import json
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def user_version(root, rev, *, launchable=True):
    destination = root / "scenes/lab-demo" / str(rev)
    shutil.copytree(ROOT / "library/scenes/lab-demo/1", destination)
    locations = yaml.safe_load((destination / "locations.yaml").read_text(encoding="utf-8"))
    locations["locations"]["rack.A1"]["p"] = [0.61, -0.31, 0.83]
    locations["locations"]["rack.A1"]["approach"] = [0.0, 0.4, -0.7, 0.0, 0.0, 0.0, 0.0]
    (destination / "locations.yaml").write_text(yaml.safe_dump(locations), encoding="utf-8")
    (destination / "validation.json").write_text(json.dumps({
        "schema": "lw.validation/1", "subject": f"scene:lab-demo@{rev}", "launchable": launchable,
        "checks": [] if launchable else [{
            "id": "P6", "tier": "physics", "status": "fail", "message": "Fixture: taught location mismatch",
        }],
    }), encoding="utf-8")
    return destination
