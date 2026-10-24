"""Native scope/high-water, typed unknowns and optional links stay data, never execution authority."""

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.fixtures.training_task import task_archive, training_plan
from tests.gateway.test_cost_http import TOKEN, admitted, report, totals
from tests.gateway.test_runtime_records import FIXTURE


@pytest.fixture
def http():
    with TestClient(create_app()) as client:
        yield client
        assert client.get("/api/v1/health").json()["sessionCount"] == 0


def native():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def post(http, records, *, format="claude-code-stream-json", **extra):
    return http.post("/api/v1/runtime-sessions/import", json={"format": format, "records": records, **extra})


def test_message_usage_is_owned_by_provider_message_not_each_block_uuid_and_zero_is_real(http):
    records = native()
    first = records[1]
    first["sessionId"] = first.pop("session_id")
    first["message"]["usage"] = {
        "input_tokens": 10,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "total_tokens": 10,
    }
    first["message"]["content"] = [{"type": "text", "text": "Message block one"}]
    second = deepcopy(first)
    second["uuid"] = "66666666-6666-4666-8666-666666666666"
    second["message"]["content"] = [{"type": "text", "text": "Message block two"}]
    imported = post(http, [first, second], format="claude-code-session-jsonl")
    assert imported.status_code == 202, imported.text
    session = imported.json()["session"]
    assert (
        session["eventCount"] == 2 and session["messageCount"] == 1 and session["usage"]["inputTokens"] == 10
    )
    assert session["usage"]["outputTokens"] == 0 and session["usage"]["cacheWriteTokens"] == 0
    assert session["usage"]["llmUsd"] is None and session["usage"]["requests"] is None
    growing = deepcopy(second)
    growing["uuid"] = "77777777-7777-4777-8777-777777777777"
    growing["message"]["usage"]["input_tokens"] = 12
    growing["message"]["usage"]["total_tokens"] = 12
    refreshed = post(http, [first, second, growing], format="claude-code-session-jsonl").json()["session"]
    assert refreshed["usage"]["inputTokens"] == 12 and refreshed["usage"]["tokens"] == 12
    assert len(http.get("/api/v1/runtime-sessions").json()["sessions"]) == 1


def test_capture_result_repeated_high_water_does_not_add_message_total_and_reports_explicit_zero(http):
    records = native()
    first = post(http, records).json()["session"]
    records[-1]["total_cost_usd"] = 0.5
    grown = post(http, records).json()
    assert grown["session"]["id"] == first["id"] and grown["updatedEvents"] == 1
    assert grown["session"]["usage"]["llmUsd"] == 0.5 and grown["session"]["eventCount"] == 4
    assert post(http, records).json()["idempotent"]
    zero = native()
    zero[0]["session_id"] = zero[1]["session_id"] = zero[2]["session_id"] = zero[3]["session_id"] = (
        "zero-session"
    )
    zero[0]["uuid"] = "zero-capture"
    zero[-1]["total_cost_usd"] = 0
    zero[-1]["usage"] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "total_tokens": 0,
    }
    observed = post(http, zero).json()["session"]["usage"]
    assert observed["llmUsd"] == observed["tokens"] == observed["cacheReadTokens"] == 0


def test_init_and_result_models_are_explicit_without_registry_or_invented_times(http):
    records = native()
    only = post(http, [records[0], records[-1]])
    assert only.status_code == 202, only.text
    session = only.json()["session"]
    assert session["models"] == ["claude-opus-5-5"]
    assert session["createdAt"] is None and session["updatedAt"] is None and session["importedAt"]


@pytest.mark.parametrize(
    "extension", ["new-message", "new-user", "message-usage", "partial-result", "new-result"]
)
def test_completed_capture_rejects_unscoped_conversation_or_usage_growth(http, extension):
    records = native()
    original = post(http, records).json()["session"]
    saved = http.app.state.runtime_records.path.read_bytes()
    message = deepcopy(records[2] if extension == "new-user" else records[1])
    if extension == "message-usage":
        message["message"]["usage"]["input_tokens"] = 1000
    else:
        message["uuid"] = "late-event"
        message["message"]["id"] = "late-message"
    incoming = [records[0], message]
    if extension == "partial-result":
        incoming.append(records[-1])
    elif extension == "new-result":
        result = deepcopy(records[-1])
        result["uuid"] = "different-result"
        incoming = [records[0], result]
    response = post(http, incoming)
    assert response.status_code == 409 and "unsupported_runtime_counter_scope" in response.text
    current = http.get(f"/api/v1/runtime-sessions/{original['id']}").json()
    assert current["eventCount"] == original["eventCount"] and current["status"] == "completed"
    assert current["usage"] == original["usage"] and current["contentHash"] == original["contentHash"]
    assert http.app.state.runtime_records.path.read_bytes() == saved
    assert http.get("/api/v1/cost/summary", params={"range": "all"}).json()["totals"]["llmUsd"] == 0.3244434


def test_completed_capture_allows_metadata_and_known_event_retries_without_result(http):
    records = native()
    original = post(http, records).json()["session"]
    metadata = {"type": "rate_limit_event", "uuid": "later-metadata", "session_id": records[0]["session_id"]}
    response = post(http, [records[0], metadata])
    assert response.status_code == 202, response.text
    current = response.json()["session"]
    assert current["eventCount"] == original["eventCount"] + 1
    assert current["usage"] == original["usage"] and current["status"] == "completed"
    assert post(http, records[:-1]).json()["idempotent"]


def test_completed_capture_backfill_needs_full_ordered_snapshot_and_nondecreasing_result(http):
    records = native()
    original = post(http, [records[0], records[-1]]).json()["session"]
    lower = deepcopy(records)
    lower[-1]["total_cost_usd"] = 0
    assert post(http, lower).status_code == 409
    response = post(http, records)
    assert response.status_code == 202, response.text
    assert response.json()["addedEvents"] == 2
    current = http.get(f"/api/v1/runtime-sessions/{original['id']}").json()
    assert [event["id"] for event in current["events"]] == [record["uuid"] for record in records]
    assert current["usage"] == original["usage"] and current["messageCount"] == 2
    assert post(http, records).json()["idempotent"]
    assert post(http, [records[0], records[-1], records[1]]).status_code == 422


def test_unrelated_runtime_import_preserves_unknown_llm_for_existing_embodied_plan():
    app = create_app(Settings(node_token=TOKEN))
    clock = [datetime.now(UTC).timestamp()]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as http:
        plan, body, job = admitted(http, clock)
        clock[0] += 10
        assert report(http, body, job, 10, "failed").status_code == 200
        filters = {"plan": f"{plan['id']}@1", "range": "all"}
        before = totals(http, **filters)
        assert before["gpuSeconds"] == 10 and before["llmUsd"] is None
        assert post(http, native()).status_code == 202
        after = totals(http, **filters)
        for field in ("requests", "tokens", "llmUsd", "inputTokens", "outputTokens", "cacheReadTokens"):
            assert after[field] is before[field] is None
        assert after["gpuSeconds"] == before["gpuSeconds"] and after["runtimeSessionCount"] == 0
        series = http.get("/api/v1/cost/series", params=filters).json()["buckets"]
        assert all(bucket["llmUsd"] is None and bucket["requests"] is None for bucket in series)
        assert totals(http)["llmUsd"] == 0.3244434
        empty = totals(http, range="custom", start="2020-01-01T00:00:00Z", end="2020-01-02T00:00:00Z")
        assert empty["llmUsd"] == empty["tokens"] == empty["requests"] == empty["runtimeSessionCount"] == 0
        assert http.get("/api/v1/health").json()["sessionCount"] == 0


def test_explicit_links_need_no_approved_plan_and_unprovided_goal_stays_unassociated(http):
    task = http.post(
        "/api/v1/tasks", content=task_archive(), headers={"Content-Type": "application/x-tar"}
    ).json()
    plan = http.post("/api/v1/plans", json=training_plan(task["id"])).json()
    before = http.get("/api/v1/approvals").json()["pendingCount"]
    imported = post(
        http,
        native(),
        associations={
            "agentId": "agent/lab-operator",
            "harnessVersion": 1,
            "planId": plan["id"],
            "planRevision": 1,
        },
    )
    assert imported.status_code == 202, imported.text
    refs = imported.json()["session"]["associations"]
    assert refs["planId"] == plan["id"] and "goalId" not in refs
    assert http.get(f"/api/v1/plans/{plan['id']}").json()["status"] == "submitted"
    assert http.get("/api/v1/approvals").json()["pendingCount"] == before
    assert post(http, native()).json()["session"]["associations"] == refs
    assert post(http, native(), associations={}).json()["session"]["associations"] == {}


@pytest.mark.parametrize(
    "change",
    [
        (3, "uuid", {}),
        (1, "role", {}),
        (1, "model", []),
        (1, "parentUuid", {}),
        (3, "is_error", "false"),
        (1, "input_tokens", -1),
        (1, "input_tokens", float("nan")),
        (1, "toolInput", float("inf")),
        (1, "toolId", {}),
        (1, "toolMapping", []),
        (1, "textBlock", {}),
    ],
)
def test_malformed_native_scalar_and_nonfinite_data_is_safe_422(http, change):
    records = native()
    index, field, value = change
    if field in {"role", "model"}:
        records[index]["message"][field] = value
    elif field == "input_tokens":
        records[index]["message"]["usage"][field] = value
    elif field == "toolInput":
        records[index]["message"]["content"][0]["input"]["not_finite"] = value
    elif field == "toolId":
        records[index]["message"]["content"][0]["id"] = value
    elif field == "toolMapping":
        records[index]["message"]["content"][0]["input"] = value
    elif field == "textBlock":
        records[index]["message"]["content"] = [{"type": "text", "text": value}]
    else:
        records[index][field] = value
    response = http.post(
        "/api/v1/runtime-sessions/import",
        json={
            "format": "claude-code-stream-json",
            "content": "\n".join(json.dumps(record) for record in records),
        },
    )
    assert response.status_code == 422, response.text
    assert http.get("/api/v1/runtime-sessions").json()["sessions"] == []


def test_model_claims_and_credentials_remain_safe_imported_text_not_platform_outcomes(http):
    records = native()
    records[1]["message"]["content"] = [
        {"type": "text", "text": "score100 approved cost0 sk-ant-FAKESECRET1234567890"},
        {
            "type": "tool_use",
            "id": "tool-secret",
            "name": "bash",
            "input": {"api_key": "PRIVATE_VALUE", "command": "no execution"},
        },
    ]
    imported = post(http, records)
    assert imported.status_code == 202, imported.text
    session = imported.json()["session"]
    trace = http.get(session["artifacts"][0]["url"]).text
    assert "PRIVATE_VALUE" not in trace and "FAKESECRET" not in trace and "score100" in trace
    assert session["usage"]["llmUsd"] == 0.3244434
    assert http.get("/api/v1/evaluations").json()["evaluations"] == []
    assert http.get("/api/v1/approvals").json()["pendingCount"] == 0


def test_cost_high_water_keeps_amount_and_source_from_the_same_runtime_evidence(http):
    records = native()
    original = post(http, records).json()["session"]
    records[-1]["total_cost_usd"] = 0
    records[-1]["modelUsage"]["claude-opus-5-5"]["costBasis"] = "provider"
    lower = post(http, records).json()["session"]
    assert lower["usage"]["llmUsd"] == original["usage"]["llmUsd"] == 0.3244434
    assert lower["usage"]["costSource"] == "runtime-estimate"
    records[-1]["total_cost_usd"] = 0.5
    greater = post(http, records).json()["session"]
    assert greater["usage"]["llmUsd"] == 0.5 and greater["usage"]["costSource"] == "runtime-reported"
    assert (
        http.get("/api/v1/runtime-sessions", params={"q": "opus-5-5"}).json()["sessions"][0]["id"]
        == greater["id"]
    )


def test_conflicting_events_or_capture_scope_are_actionable_and_cannot_read_server_paths(http):
    records = native()
    initial = post(http, records).json()["session"]
    conflicting = deepcopy(records)
    conflicting[1]["message"]["content"][0]["input"]["query"] = "Changed native event"
    response = post(http, conflicting)
    assert response.status_code == 409 and "runtime_event_conflict" in response.text
    another_capture = deepcopy(records)
    another_capture[0]["uuid"] = "different-native-capture"
    assert post(http, another_capture).status_code == 409
    assert post(http, records, format="unsupported-runtime-format").status_code == 422
    secret_path = "PRIVATE_SERVER_FILE_MUST_NOT_READ"
    invalid = http.post(
        "/api/v1/runtime-sessions/import",
        json={"format": "claude-code-stream-json", "records": records, "path": secret_path},
    )
    assert invalid.status_code == 422 and secret_path not in invalid.text
    current = http.get(f"/api/v1/runtime-sessions/{initial['id']}").json()
    assert current["contentHash"] == initial["contentHash"]
