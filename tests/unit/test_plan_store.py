"""Durable plan history cannot become a grant for sessions lost during restart."""

import pytest

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.approvals import ApprovalStore
from packages.info.plans import PlanStore, StoredPlan
from packages.protocol.schema import Actor, OperationPlan


async def test_interrupted_append_recovers_history_and_abandons_unowned_session_grants(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    approvals.startup()
    actor = Actor(kind="agent", name="operator")
    approval = approvals.submit(
        subject="plan",
        subject_id="plan-one",
        requester=actor,
        title="Run",
        session_id="session-no-longer-owned",
        details={"revision": 1},
    )
    store = PlanStore(tmp_path / "plans.jsonl", approvals)
    store.startup()
    record = StoredPlan(
        plan=OperationPlan(
            id="plan-one",
            title="Run",
            target="session",
            revision=1,
            status="submitted",
            steps=[{"id": 1, "check": {"entity": "/World/Device", "property": "ready", "equals": True}}],
        ),
        sessionId="session-no-longer-owned",
        sceneRevision="rev-previous",
        epoch=0,
        requestedBy=actor,
        createdAtMs=1000,
        humanRequired=True,
        approvalId=approval.approvalId,
    )
    store._save(record)
    await store.flush()
    await store.close()
    with store.path.open("ab") as stream:
        stream.write(b'{"plan":')
    recovered = PlanStore(store.path, approvals)
    recovered.startup()
    await recovered.flush()
    assert recovered.get("plan-one").plan.status == "abandoned"
    assert recovered.get("plan-one", 1).plan.steps[0].status == "pending"
    assert approvals.records[approval.approvalId].status == "unavailable"
    await recovered.close()
    again = PlanStore(store.path, approvals)
    again.startup()
    assert again.get("plan-one") == recovered.get("plan-one")
    assert again.bindings == {} and again.requests == {}
    await again.close()


async def test_startup_failure_after_recovery_closes_the_owned_journal(tmp_path, monkeypatch):
    app = create_app(Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"))
    store = app.state.manager.plans
    record = StoredPlan(
        plan=OperationPlan(
            id="plan-startup",
            title="Recover",
            target="session",
            revision=1,
            status="approved",
            steps=[{"id": 1, "check": {"entity": "/World/Device", "property": "ready", "equals": True}}],
        ),
        sessionId="old-session",
        sceneRevision="old-revision",
        epoch=0,
        requestedBy=Actor(),
        createdAtMs=1,
    )
    store.path.parent.mkdir()
    store.path.write_text(record.model_dump_json() + "\n", "utf-8")

    def fail_startup():
        raise RuntimeError("Injected library startup failure")

    monkeypatch.setattr(app.state.library_assets, "startup", fail_startup)
    with pytest.raises(RuntimeError, match="Injected library startup failure"):
        async with app.router.lifespan_context(app):
            raise AssertionError("Startup must not accept traffic")
    assert store.get("plan-startup").plan.status == "abandoned"
    assert store.journal._thread is not None and not store.journal._thread.is_alive()
