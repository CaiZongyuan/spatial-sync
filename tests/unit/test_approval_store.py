"""The subject envelope and durable audit preserve decisions across interrupted writes."""

import pytest

from packages.info.approvals import ApprovalStore
from packages.protocol.schema import Actor


def test_store_opens_lazily_and_recovers_partial_last_write_without_reopening_requests(tmp_path):
    path = tmp_path / "state" / "approvals.jsonl"
    store = ApprovalStore(path)
    assert not path.parent.exists()
    store.startup()
    user = Actor(kind="user", name="operator")
    decided = store.submit(subject="policy", subject_id="policy/fr3@2", requester=user, title="Promote")
    store.decide(decided.approvalId, "deny", user, "需要更多评估")
    pending = store.submit(subject="invocation", subject_id="inv-1", requester=user, title="Run")
    durable = store.submit(subject="deletion", subject_id="scene/lab@1", requester=user, title="Remove")
    with path.open("ab") as stream:
        stream.write(b'{"approvalId":"partial')
    recovered = ApprovalStore(path)
    recovered.startup()
    assert recovered.records[decided.approvalId].status == "denied"
    assert recovered.records[decided.approvalId].decisions[0].note == "需要更多评估"
    assert recovered.records[pending.approvalId].status == "unavailable"
    assert recovered.records[durable.approvalId].status == "pending"
    again = ApprovalStore(path)
    again.startup()
    assert again.records == recovered.records


@pytest.mark.parametrize("subject", ["invocation", "plan", "policy", "job", "deletion"])
def test_all_approved_subject_types_have_the_same_envelope_without_executing_future_work(tmp_path, subject):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    record = store.submit(subject=subject, subject_id="resource-1", requester=Actor(), title="Human decision")
    assert record.subject == subject and record.status == "pending" and record.decisions == []
