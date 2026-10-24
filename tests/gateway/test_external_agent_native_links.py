"""Native metering stays imported data and completion snapshots remain immutable."""

import json

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.gateway.test_external_agent_evaluation import BASE, create, work
from tests.gateway.test_runtime_records import FIXTURE


def test_explicit_native_link_validates_identity_and_freezes_cost_without_double_count():
    settings = Settings()
    declaration = {"name": "claude-code", "version": None, "model": "claude-opus-5-5", "thinking": "high"}
    records = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    with TestClient(create_app(settings)) as http:
        run, _ = create(http, runtime=declaration)
        item = work(http, run)
        native = http.post(
            "/api/v1/runtime-sessions/import",
            json={
                "format": "claude-code-stream-json",
                "records": records,
            },
        ).json()["session"]
        body = {
            "caseRunId": item["caseRunId"],
            "nativeSessionId": native["nativeSessionId"],
            "runtimeRecordId": native["id"],
        }
        wrong = http.post(BASE + run["id"] + "/complete", json=body | {"nativeSessionId": "different-native"})
        assert wrong.status_code == 409 and "runtime_context_mismatch" in wrong.text
        outcome = http.post(BASE + run["id"] + "/complete", json=body).json()["case"]
        assert outcome["status"] == "completed" and outcome["runtimeRecordHash"] == native["contentHash"]
        assert outcome["usage"]["llmUsd"] == 0.3244434 and outcome["usage"]["requests"] is None
        assert outcome["usage"]["tokens"] is None and outcome["usage"]["costSource"] == "runtime-estimate"
        assert outcome["usage"]["observationBasis"] == "completion-snapshot"
        assert outcome["observedRuntime"]["version"] == "2.1.306"
        records[-1]["total_cost_usd"] = 0.5
        refreshed = http.post(
            "/api/v1/runtime-sessions/import",
            json={
                "format": "claude-code-stream-json",
                "records": records,
            },
        ).json()["session"]
        assert refreshed["id"] == native["id"] and refreshed["contentHash"] != native["contentHash"]
        assert http.post(BASE + run["id"] + "/complete", json=body).json()["case"] == outcome
        cost = http.get("/api/v1/cost/summary", params={"range": "all"}).json()["totals"]
        assert cost["llmUsd"] == 0.5 and cost["runtimeSessionCount"] == 1
        public = http.get(BASE + run["id"]).json()
        assert item["connection"]["token"] not in json.dumps(public)
        assert public["cases"][0]["runtimeRecordHash"] == native["contentHash"]
    with TestClient(create_app(settings)) as restarted:
        saved = restarted.get(BASE + run["id"]).json()
        assert saved["cases"][0] == outcome
        assert saved["status"] == "completed" and saved.get("result") is None
        assert restarted.get("/api/v1/suites/lab-ops-basic/1/scoreboard").json()["rows"] == []
