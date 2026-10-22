"""Real Synthetic browser fixture: declare one action high-risk before host startup.

Browser cases import packages and write approvals and recordings through this gateway.
Unless the caller selects them, its library, state and recordings roots default to this
checkout's `.scratch/viewer-e2e` instead of the operator's own defaults.
"""

import asyncio
import os
from pathlib import Path

OWNED_ROOT = Path(__file__).resolve().parents[2] / ".scratch" / "viewer-e2e"
for variable, directory in (
    ("LW_LIBRARY", "library"), ("SPATIAL_STATE_ROOT", "state"), ("SPATIAL_RECORDINGS_ROOT", "recordings")
):
    os.environ.setdefault(variable, str(OWNED_ROOT / directory))

from apps.api.app.main import app  # noqa: E402 - the gateway reads its roots on import.
from apps.api.app.sources import synthetic_source  # noqa: E402
from packages.info.catalog import load_catalog  # noqa: E402

__all__ = ["app"]


def approval_catalog(manifest, scene_id, registry, **options):
    catalog = load_catalog(manifest, scene_id, registry, **options)
    catalog.types["centrifuge/v1"]["actions"]["closeLid"]["risk"] = "high"
    return catalog


synthetic_source.load_catalog = approval_catalog


@app.get("/e2e/storage-roots")
async def storage_roots():
    # E2E global setup refuses a gateway whose actual write roots are shared.
    settings = app.state.settings
    return {
        "sceneLibrary": str(app.state.manager.library.roots["user"]),
        "assetLibrary": str(app.state.library_assets.roots["user"]),
        "state": str(settings.state_root.resolve()),
        "recordings": str(settings.recordings_root.resolve()),
    }


@app.post("/e2e/evaluation-alert-suite")
async def evaluation_alert_suite():
    from tests.fixtures.evaluation_alerts import prepare_suite

    manager = app.state.manager
    scene = manager.library.resolve("evaluation-alerts", launch=True, source="synthetic")
    suite = await asyncio.to_thread(prepare_suite, manager.library.roots["user"], scene)
    await asyncio.to_thread(manager.evaluations.suites.refresh)
    return {"suite": suite, "sceneVersion": scene.id, "sceneContentHash": scene.content_hash}
