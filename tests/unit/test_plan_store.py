"""Durable plan history cannot become a grant for sessions lost during restart."""

from packages.info.approvals import ApprovalStore
from packages.info.plans import PlanStore, StoredPlan
from packages.protocol.schema import Actor, OperationPlan


def test_interrupted_append_recovers_history_and_abandons_unowned_session_grants(tmp_path):
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
    with store.path.open("ab") as stream:
        stream.write(b'{"plan":')
    recovered = PlanStore(store.path, approvals)
    recovered.startup()
    assert recovered.get("plan-one").plan.status == "abandoned"
    assert recovered.get("plan-one", 1).plan.steps[0].status == "pending"
    assert approvals.records[approval.approvalId].status == "unavailable"
    again = PlanStore(store.path, approvals)
    again.startup()
    assert again.get("plan-one") == recovered.get("plan-one")
    assert again.bindings == {} and again.requests == {}
