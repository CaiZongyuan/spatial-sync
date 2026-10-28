"""Browser fixture: the real gateway, external reports and an unsubmitted Blender edit.

``cart-isolation@2`` is a received scene with a failed platform check: the cart
is at 0.00 m. A draft report says the external Blender working file is at 0.20 m;
that file was never exported, so the platform keeps v2. ``POST /e2e/sf4/receive-v3``
places the next externally produced package in the user library *without* a
platform validation report (the normal operator-installed state). The browser
journey then revalidates it through the real HTTP endpoint, producing v3's own
report; v2, its report and its recording remain untouched.

No network, Blender, physics engine or attachment is used.
"""

import json
import os
import shutil
from pathlib import Path

import yaml

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import content_hash

ROOT = Path(__file__).resolve().parents[2]
OWNED = ROOT / ".scratch/sf4-e2e"
NAME = "cart-isolation"
os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
os.environ["SPATIAL_CATALOG_ROOT"] = ""
for variable, directory in (
    ("LW_LIBRARY", "library"),
    ("SPATIAL_STATE_ROOT", "state"),
    ("SPATIAL_RECORDINGS_ROOT", "recordings"),
):
    os.environ.setdefault(variable, str(OWNED / directory))

LIBRARY = Path(os.environ["LW_LIBRARY"])
RECORDINGS = Path(os.environ["SPATIAL_RECORDINGS_ROOT"])


def write_package(rev, offset):
    destination = LIBRARY / f"scenes/{NAME}/{rev}"
    shutil.copytree(ROOT / "library/scenes/lab-demo/1", destination)
    metadata = yaml.safe_load((destination / "scene.yaml").read_text(encoding="utf-8"))
    metadata |= {
        "name": NAME,
        "title": "Cart isolation fixture",
        "description": f"Received package: cart at {offset:.2f} m",
    }
    (destination / "scene.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    bindings = yaml.safe_load((destination / "devices.yaml").read_text(encoding="utf-8"))
    bindings["scene"] = NAME
    (destination / "devices.yaml").write_text(yaml.safe_dump(bindings), encoding="utf-8")
    (destination / "validation.json").unlink(missing_ok=True)
    return destination


def seed():
    v2 = write_package(2, 0.00)
    (v2 / "validation.json").write_text(json.dumps({
        "schema": "lw.validation/1",
        "subject": f"scene:{NAME}@2",
        "ranAt": "2026-10-24T10:00:00+00:00",
        "toolVersions": {},
        "checks": [{
            "id": "P3", "tier": "physics", "status": "fail",
            "message": "台车位移 0.00 m，超过 0.10 m 上限",
            "entityKey": "/World/Lab/Cart",
        }],
        "launchable": False,
    }, ensure_ascii=False), encoding="utf-8")
    recording = RECORDINGS / "session-cart-v2"
    shutil.copytree(ROOT / "tests/fixtures/recordings/v2", recording)
    metadata = json.loads((recording / "recording.json").read_text(encoding="utf-8"))
    metadata |= {
        "sessionId": recording.name,
        "sceneId": NAME,
        "sceneVersion": f"{NAME}@2",
        "sceneContentHash": content_hash(v2),
        "createdAt": "2026-10-24T10:05:00+00:00",
    }
    (recording / "recording.json").write_text(json.dumps(metadata), encoding="utf-8")


seed()
app = create_app(Settings())


@app.get("/e2e/storage-roots")
async def storage_roots():
    manager = app.state.manager
    return {
        "sceneLibrary": str(manager.library.roots["user"]),
        "assetLibrary": str(app.state.library_assets.roots["user"]),
        "state": str(manager.settings.state_root),
        "recordings": str(manager.settings.recordings_root),
    }


@app.post("/e2e/sf4/receive-v3")
async def receive_v3():
    """Simulate the external factory handing over its next package.

    The package intentionally has no validation report; the platform has not
    validated it yet. This is not an import or validation claim.
    """
    v3 = write_package(3, 0.20)
    app.state.manager.library.refresh()
    return {"sceneVersion": f"{NAME}@3", "contentHash": content_hash(v3)}
