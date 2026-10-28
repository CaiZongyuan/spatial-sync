"""CLI/MCP fixture: the real gateway with only externally submitted reports.

The report store, library binding checks and routes are the production ones. No
network, Blender, physics engine or attachment is involved.
"""

import os
from pathlib import Path

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
OWNED = ROOT / ".scratch/authoring-e2e"
os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
os.environ["SPATIAL_CATALOG_ROOT"] = ""
for variable, directory in (
    ("LW_LIBRARY", "library"),
    ("SPATIAL_STATE_ROOT", "state"),
    ("SPATIAL_RECORDINGS_ROOT", "recordings"),
):
    os.environ.setdefault(variable, str(OWNED / directory))

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
