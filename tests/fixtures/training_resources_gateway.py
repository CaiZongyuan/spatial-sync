"""GPU-free browser fixture; all Job and fake-node writes use real HTTP routes."""

import os
import time
from pathlib import Path

from apps.api.app.main import create_app
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system
from apps.api.app.settings import Settings
from packages.training.jobs import profile_key

ROOT = Path(__file__).resolve().parents[2]
os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
for variable, name in (("LW_LIBRARY", "library"), ("SPATIAL_STATE_ROOT", "state"),
                       ("SPATIAL_RECORDINGS_ROOT", "recordings")):
    os.environ.setdefault(variable, str(ROOT / ".scratch/training-resources-e2e" / name))

TOKEN = "training-resources-browser-fixture-token"
HARDWARE = "GPU-free browser node; no training is executed"
GPU_ID = "GPU-training-resources-fixture"
INSTANCE = "training-resources-browser-node"


class Sampler:
    def sample(self, rows):
        return platform_snapshot(
            rows, unavailable_system("Host probes disabled in the GPU-free browser fixture")
        )


allowed_origins = tuple(filter(None, os.getenv("SPATIAL_ALLOWED_ORIGINS", "").split(",")))
app = create_app(
    Settings(
        node_token=TOKEN, node_trainer=(), allowed_origins=allowed_origins or Settings().allowed_origins
    ),
    platform_sampler=Sampler(),
)


@app.get("/e2e/storage-roots")
async def storage_roots():
    manager = app.state.manager
    return {"sceneLibrary": str(manager.library.roots["user"]),
            "assetLibrary": str(app.state.library_assets.roots["user"]),
            "state": str(manager.settings.state_root), "recordings": str(manager.settings.recordings_root)}


@app.get("/e2e/training-profile/{plan_id}")
async def training_profile(plan_id: str):
    """Return a declared test measurement, without touching admission or lease state."""
    manager = app.state.manager
    plan = manager.training_plans.document(manager.training_plans.get(plan_id))
    task = manager.tasks.show(plan["task"])
    return {
        "instanceId": INSTANCE, "hardware": HARDWARE, "cpuAvailable": False,
        "gpus": [{"id": GPU_ID, "name": "Browser fixture GPU", "memoryTotalMb": 8192}],
        "profiles": [{"key": profile_key(plan, task["contentHash"], HARDWARE, GPU_ID),
                      "iterationSeconds": 6, "setupSeconds": 10, "samples": 10,
                      "measuredAt": time.time(), "evidence": "Declared GPU-free browser test input"}],
    }
