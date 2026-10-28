"""CLI/browser fixture: the real acquisition service with a deterministic runner.

The fake runner stages the committed package under ``tests/fixtures/acquisition``
using the same progress/result contract as the real worker. No network, factory
import, Blender or physics engine is involved. Tests can set the per-phase delay
through ``POST /e2e/acquisition-delay`` to observe progress and cancel.

``LW_ACQUISITION_FIXTURE_BUDGET=1`` starts the same app with a deliberately
tiny batch budget so the public ``budget_exceeded`` envelope can be exercised.
"""

import asyncio
import os
import shutil
from pathlib import Path

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.acquisition import Limits
from packages.acquisition.runner import AcquisitionCancelled, StagedResult

ROOT = Path(__file__).resolve().parents[2]
OWNED = ROOT / ".scratch/acquisition-e2e"
os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
for variable, directory in (
    ("LW_LIBRARY", "library"),
    ("SPATIAL_STATE_ROOT", "state"),
    ("SPATIAL_RECORDINGS_ROOT", "recordings"),
):
    os.environ.setdefault(variable, str(OWNED / directory))

PACKAGE = Path(__file__).with_name("acquisition") / "package"
DEFAULT_DELAY = 0.05
MAX_DELAY = 30.0
TINY_BATCH = os.environ.get("LW_ACQUISITION_FIXTURE_BUDGET") == "1"


class FixtureRunner:
    """Report two phases, stage a real package and honour cancellation."""

    def __init__(self):
        self.delay = DEFAULT_DELAY
        self.staging_root = Path(os.environ["LW_LIBRARY"]) / ".acquisition-staging"

    async def _hold(self, cancel):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.delay
        while loop.time() < deadline:
            if cancel.is_set():
                raise AcquisitionCancelled()
            await asyncio.sleep(min(0.02, max(0.0, deadline - loop.time())))
        if cancel.is_set():
            raise AcquisitionCancelled()

    @staticmethod
    def _package_bytes():
        return sum(
            entry.stat().st_size
            for entry in PACKAGE.rglob("*")
            if entry.is_file() and not entry.is_symlink()
        )

    async def run(self, record, progress, cancel):
        total = record.estimate["source"]["bytes"]
        files = record.estimate["source"]["files"]
        await progress(
            {
                "phase": "downloading",
                "completedBytes": total // 2,
                "totalBytes": total,
                "completedFiles": 1,
                "totalFiles": files,
                "message": "Fixture source is copied locally; no network is used",
            }
        )
        await self._hold(cancel)
        await progress(
            {
                "phase": "preparing",
                "completedBytes": total,
                "totalBytes": total,
                "completedFiles": files,
                "totalFiles": files,
                "message": "Fixture adaptation is a directory copy",
            }
        )
        stage = self.staging_root / record.id / "package"
        if stage.exists():
            shutil.rmtree(stage)
        stage.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(PACKAGE, stage)
        await self._hold(cancel)
        return StagedResult(
            stage,
            usage={
                "sourceBytes": total,
                "transferBytes": total,
                "cacheReusedBytes": 0,
                "adaptedBytes": self._package_bytes(),
            },
        )


runner = FixtureRunner()
allowed_origins = tuple(filter(None, os.getenv("SPATIAL_ALLOWED_ORIGINS", "").split(",")))
app = create_app(
    Settings(allowed_origins=allowed_origins or Settings().allowed_origins),
    acquisition_runner=runner,
    acquisition_limits=Limits(batch_bytes=1) if TINY_BATCH else None,
)


@app.get("/e2e/storage-roots")
async def storage_roots():
    manager = app.state.manager
    return {
        "sceneLibrary": str(manager.library.roots["user"]),
        "assetLibrary": str(app.state.library_assets.roots["user"]),
        "state": str(manager.settings.state_root),
        "recordings": str(manager.settings.recordings_root),
    }


@app.post("/e2e/acquisition-delay")
async def acquisition_delay(body: dict):
    try:
        delay = float(body.get("delaySeconds", DEFAULT_DELAY))
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY
    runner.delay = min(max(delay, 0.0), MAX_DELAY)
    return {"delaySeconds": runner.delay}
