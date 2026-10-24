"""Controlled owned files use the real fixed publisher and public serving route."""

import json

from tests.gateway.test_agent_run_budgets import metadata as metadata
from tests.gateway.test_agent_run_budgets import request
from tests.gateway.test_agent_runs import configured


async def test_actual_publisher_serves_only_named_case_evidence_and_refuses_changed_or_private_files(
    metadata,
):
    app, http, _ = metadata
    plan = await configured(http, app)
    run = (await request(http, plan, "evidence-fixture")).json()
    service = app.state.agent_runs
    case_id = "case-0001"
    service.update(
        run["jobId"],
        caseRuns=[
            {
                "caseRunId": case_id,
                "caseId": "controlled-public-case",
                "repetition": 1,
                "status": "valid",
                "score": 40,
            }
        ],
    )
    directory = service.root / run["jobId"] / "cases" / case_id
    (directory / "worker").mkdir(parents=True)
    (directory / "trace.jsonl").write_text('{"kind":"case.final","source":"platform"}\n', encoding="utf-8")
    (directory / "worker/trace.jsonl").write_text('{"kind":"message","source":"sdk"}\n', encoding="utf-8")
    for name in ("final.json", "score.json", "usage.json"):
        (directory / name).write_text(json.dumps({"controlled": name}), encoding="utf-8")
    canary = "PRIVATE_TRANSPORT_REQUEST_GRANT_MUST_NOT_EXPORT"
    (directory / "worker/request.json").write_text(canary, encoding="utf-8")
    private = service.root / run["jobId"] / "private-transport"
    private.mkdir()
    (private / "observations.jsonl").write_text(canary, encoding="utf-8")
    artifacts = service.publish_case(run["jobId"], case_id)
    assert {item["kind"] for item in artifacts} == {
        "platform-trace",
        "sdk-messages",
        "final-state",
        "score",
        "usage",
    }
    assert not any("observations" in item["path"] or "request.json" in item["path"] for item in artifacts)
    for item in artifacts:
        response = await http.get(item["url"])
        assert response.status_code == 200 and canary not in response.text
        assert len(response.content) == item["size"] and response.headers["cache-control"] == "no-store"
    for name in ("observations.jsonl", "request.json", "case-0001-private-transport"):
        denied = await http.get(f"/api/v1/agent-runs/{run['jobId']}/artifacts/{name}")
        assert denied.status_code == 404 and canary not in denied.text
    (directory / "trace.jsonl").write_text('{"changed":true}\n', encoding="utf-8")
    trace = next(item for item in artifacts if item["kind"] == "platform-trace")
    changed = await http.get(trace["url"])
    assert changed.status_code == 409 and "agent_evidence_changed" in changed.text


async def test_agent_logs_read_only_public_journal_with_stable_cursor(metadata):
    app, http, _ = metadata
    plan = await configured(http, app)
    run = (await request(http, plan, "public-logs")).json()
    service = app.state.agent_runs
    service.append_log(run["jobId"], "case-0001", {"kind": "model.request", "data": {"public": True}})
    first = await http.get(f"/api/v1/jobs/{run['jobId']}/logs")
    assert first.status_code == 200 and first.json()["cursor"] == 1
    service.append_log(run["jobId"], "case-0002", {"kind": "case.final", "data": {"public": True}})
    second = await http.get(f"/api/v1/jobs/{run['jobId']}/logs", params={"after": 1})
    assert len(second.json()["lines"]) == 1 and second.json()["cursor"] == 2
    assert json.loads(second.json()["lines"][0])["caseRunId"] == "case-0002"


async def test_agent_and_failed_request_anomalies_use_safe_codes_and_same_run_receipts(metadata):
    app, http, _ = metadata
    plan = await configured(http, app)
    run = (await request(http, plan, "failure-anomalies")).json()
    attempt = {
        "id": run["jobId"] + "/request/1",
        "at": run["createdAt"],
        "status": "failed",
        "usageComplete": False,
        "errorCode": "provider_interrupted",
    }
    app.state.agent_runs.update(
        run["jobId"],
        status="failed",
        reason="agent_sdk_failed",
        finishedAt=run["createdAt"],
        provider={"requests": [attempt]},
    )
    anomalies = (await http.get("/api/v1/cost/anomalies")).json()["anomalies"]
    assert {row["kind"] for row in anomalies} == {"job", "llm"}
    assert {row["detail"] for row in anomalies} == {
        "agent_sdk_failed",
        "provider_interrupted",
        "usage_incomplete",
    }
    model = next(row for row in anomalies if row["id"].startswith("llm:"))
    assert (await http.post(f"/api/v1/cost/anomalies/{model['id']}/read")).status_code == 200
