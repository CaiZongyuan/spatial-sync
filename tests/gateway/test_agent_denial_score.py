"""One actual policy denial flows through trusted Trace into the unchanged private rubric."""

from packages.info.agent_grading import final_observations, grade_agent_case
from packages.library.common import REPO_ROOT
from packages.library.suites import SuiteLibrary
from tests.gateway.test_agent_audit import audited as audited
from tests.gateway.test_agent_audit import call
from tests.gateway.test_agent_permissions import (
    declared_high_risk_close_lid as declared_high_risk_close_lid,
)


async def test_actual_high_risk_denial_changes_private_process_score_without_creating_human_approval(
    audited, tmp_path
):
    mcp, http, session, audit = audited
    async with mcp:
        denied = await call(mcp, "invoke_action", key="/World/Lab/Centrifuge", action="closeLid")
    assert denied["status"] == "rejected" and denied["error"]["code"] == "approval_denied"
    assert (await http.get("/api/v1/approvals")).json()["pendingCount"] == 0
    final = await http.get(f"/api/v1/sessions/{session.session_id}/state")
    summary = (await http.get(f"/api/v1/sessions/{session.session_id}")).json()
    assert final.json()["entities"]["/World/Lab/Centrifuge"]["values"]["lidOpen"] is True
    audit.seal(
        {"state": final.json(), "scope": audit.scope()},
        observed_monotonic=float(final.headers["X-LW-Observed-Monotonic"]),
    )
    suites = SuiteLibrary({"builtin": REPO_ROOT / "library", "user": tmp_path / "unused-library"})
    suites.refresh()
    suite = suites.resolve("suite/lab-ops-basic@1")
    values, metadata = final_observations(final.json(), summary, [])
    elapsed = audit.clock() - audit.started
    baseline = grade_agent_case(suite, "centrifuge-two", values, [], elapsed, metadata)
    actual = grade_agent_case(suite, "centrifuge-two", values, audit.derived(), elapsed, metadata)
    assert actual["metrics"]["rejectedHighRisk"] == 1
    assert actual["dimensions"]["rejectedHighRisk"] == 0
    assert baseline["dimensions"]["rejectedHighRisk"] == 20
    assert actual["score"] == baseline["score"] - 20
