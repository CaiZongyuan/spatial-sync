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


@app.post("/e2e/approval-pages")
async def approval_pages(payload: dict):
    from packages.protocol.schema import Actor

    store = app.state.manager.approvals
    if payload.get("clear"):
        for record in list(store.records.values()):
            if record.requestedBy.name == "approval-page-fixture" and record.status == "pending":
                store.set_status(record.approvalId, "unavailable")
        return {"cleared": True}
    count = payload.get("count", 0)
    if type(count) is not int or not 1 <= count <= 120:
        from fastapi import HTTPException
        raise HTTPException(422, "count must be 1..120")
    records = []
    for index in range(count):
        record = store.submit(
            subject="deletion", subject_id=f"scene:pagination-fixture@{index + 1}",
            title=f"[controlled pagination] {index:03}",
            requester=Actor(kind="cli", name="approval-page-fixture"),
            details={"resource": "scene", "id": f"pagination-fixture@{index + 1}", "root": "user",
                     "contentHash": "a" * 64,
                     "inspection": {"complete": True, "references": [], "uninspected": [],
                                    "coverage": [], "training": "Controlled paging fixture"},
                     "execution": {"status": "not_started"}},
        )
        records.append(record.approvalId)
    return {"ids": records}


@app.post("/e2e/evaluation-alert-suite")
async def evaluation_alert_suite(payload: dict | None = None):
    from tests.fixtures.evaluation_alerts import prepare_suite
    from tests.fixtures.evaluation_barrier import EvaluationAlertBarriers

    manager = app.state.manager
    scene = manager.library.resolve((payload or {}).get("sceneVersion", "evaluation-alerts"),
                                    launch=True, source="synthetic")
    suite = await asyncio.to_thread(prepare_suite, manager.library.roots["user"], scene)
    await asyncio.to_thread(manager.evaluations.suites.refresh)
    if not hasattr(app.state, "evaluation_alert_barriers"):
        app.state.evaluation_alert_barriers = EvaluationAlertBarriers(manager.evaluations)
    return app.state.evaluation_alert_barriers.register(manager.evaluations.suites.resolve(suite))


@app.post("/e2e/evaluation-alert-barriers/{token}/bind")
async def bind_evaluation_alert_barrier(token: str, payload: dict):
    return app.state.evaluation_alert_barriers.bind(token, payload.get("evaluationId"))


@app.get("/e2e/evaluation-alert-barriers/{token}")
async def evaluation_alert_barrier(token: str):
    return app.state.evaluation_alert_barriers.snapshot(token)


@app.post("/e2e/evaluation-alert-barriers/{token}/release")
async def release_evaluation_alert_barrier(token: str):
    return app.state.evaluation_alert_barriers.release(token)


@app.post("/e2e/training-node")
async def training_node(payload: dict):
    from packages.training.job_contract import NodeHeartbeat
    from packages.training.jobs import profile_key

    manager = app.state.manager
    plan = manager.training_plans.document(manager.training_plans.get(payload["planId"]))
    task = manager.tasks.show(plan["task"])
    owner = manager.jobs.nodes.get("local", {})
    body = {"instanceId": owner.get("instanceId", "browser-budget-fixture"),
            "hardware": "GPU-free browser budget fixture",
            "gpus": [{"id": "fixture-gpu", "name": "No physical GPU is used"}],
            "profiles": [{"key": profile_key(plan, task["contentHash"], "GPU-free browser budget fixture",
                                              "fixture-gpu"), "iterationSeconds": 36, "setupSeconds": 0,
                          "samples": 1, "measuredAt": 1, "evidence": "Controlled test input"}],
            "reports": payload.get("reports", [])}
    return manager.jobs.heartbeat(NodeHeartbeat.model_validate(body))
