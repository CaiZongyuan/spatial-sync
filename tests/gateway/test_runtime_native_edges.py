"""Native scope/high-water, typed unknowns and optional links stay data, never execution authority."""

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from tests.fixtures.training_task import task_archive, training_plan
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
    assert session["eventCount"] == 2 and session["usage"]["inputTokens"] == 10
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
