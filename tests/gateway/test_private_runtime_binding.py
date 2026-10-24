import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.agent_rsi.budgets import public_cost


def test_durable_private_binding_hides_existing_capture_before_flag_publication_and_after_restart():
    settings = Settings()
    marker = "PRIVATE-DURABLE-BINDING-CANARY"
    source = Path(__file__).resolve().parents[1] / "fixtures/runtime/claude-code-stream.jsonl"
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    records[1]["message"]["content"] = [{"type": "text", "text": marker}]
    app = create_app(settings)

    def assert_hidden(http, current):
        assert current.state.runtime_records.get(identity).get("visibility") != "private"
        assert http.get("/api/v1/runtime-sessions/" + identity).status_code == 404
        assert http.get("/api/v1/runtime-sessions/" + identity + "/artifacts/transcript").status_code == 404
        assert identity not in http.get("/api/v1/runtime-sessions").text
        detail = http.get("/api/v1/cost/breakdown", params={"range": "all", "by": "agent"})
        assert detail.status_code == 200 and identity not in detail.text and marker not in detail.text
        summary = http.get("/api/v1/cost/summary", params={"range": "all", "goal": "goal/private-binding"})
        assert summary.json()["totals"]["llmUsd"] == 0.3244434
        projected = public_cost(
            current.state.agent_rsi,
            {
                "llmUsd": 0.3244434,
                "runtimeRecordIds": [identity],
                "runtimeRecordHashes": {identity: "old-hash"},
            },
        )
        assert projected["runtimeRecordIds"] == [] and projected["runtimeRecordHashes"] == {}
        assert projected["llmUsd"] == 0.3244434 and projected["detailsCoverage"] == "restricted"
        assert http.get("/api/v1/health").json()["sessionCount"] == 0

    with TestClient(app) as http:
        imported = http.post(
            "/api/v1/runtime-sessions/import", json={"format": "claude-code-stream-json", "records": records}
        )
        assert imported.status_code == 202, imported.text
        identity = imported.json()["session"]["id"]
        native = imported.json()["session"]["nativeSessionId"]
        assert marker in http.get("/api/v1/runtime-sessions/" + identity).text
        context = {
            "promotionId": "promotion-interrupted",
            "caseRunId": "case-interrupted",
            "evaluationId": "eval-private",
            "agentId": "agent/lab-operator",
            "harnessVersion": 1,
            "planId": "plan-private",
            "planRevision": 1,
            "goalId": "goal/private-binding",
        }
        # Bind_native's first durable publication survives interruption before mark_private.
        app.state.agent_rsi._write(
            promotions={
                "promotion-interrupted": {
                    "id": "promotion-interrupted",
                    "status": "evaluating",
                    "planId": "plan-private",
                    "runs": {},
                    "nativeBindings": {native: context},
                }
            }
        )
        assert_hidden(http, app)
    restarted = create_app(settings)
    with TestClient(restarted) as http:
        assert_hidden(http, restarted)
