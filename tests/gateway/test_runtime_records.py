"""Actual-format file -> HTTP intake -> durable restart/readback; no runtime or Source is launched."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/runtime/claude-code-stream.jsonl"


def test_actual_claude_capture_persists_reads_and_reimports_without_double_counting():
    settings = Settings()
    content = FIXTURE.read_text(encoding="utf-8")
    records = [json.loads(line) for line in content.splitlines()]
    with TestClient(create_app(settings)) as http:
        partial = http.post(
            "/api/v1/runtime-sessions/import",
            json={
                "format": "claude-code-stream-json",
                "records": records[:-1],
                "sourceLabel": "captured-session.jsonl",
            },
        )
        assert partial.status_code == 202, partial.text
        assert partial.json()["session"]["usage"]["llmUsd"] is None
        imported = http.post(
            "/api/v1/runtime-sessions/import", json={"format": "claude-code-stream-json", "content": content}
        )
        assert imported.status_code == 202, imported.text
        session = imported.json()["session"]
        assert session["id"] == partial.json()["session"]["id"] and imported.json()["addedEvents"] == 1
        assert (
            session["usage"]["llmUsd"] == 0.3244434 and session["usage"]["costSource"] == "runtime-estimate"
        )
        assert session["usage"]["inputTokens"] == 32 and session["usage"]["outputTokens"] == 2680
        assert session["usage"]["cacheWriteTokens"] == 26878 and session["usage"]["cacheReadTokens"] == 278457
        assert session["usage"]["tokens"] is None and session["usage"]["requests"] is None
        assert session["usage"]["turns"] == 20 and session["associations"] == {}
        saved = (settings.state_root / "runtime-sessions.json").read_bytes()
        repeated = http.post(
            "/api/v1/runtime-sessions/import", json={"format": "claude-code-stream-json", "records": records}
        )
        assert repeated.json()["idempotent"] and repeated.json()["addedEvents"] == 0
        assert (settings.state_root / "runtime-sessions.json").read_bytes() == saved
        detail = http.get(f"/api/v1/runtime-sessions/{session['id']}").json()
        assert len(detail["events"]) == 4 and detail["events"][0]["timestamp"] is None
        assert detail["createdAt"] != detail["importedAt"]
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
        assert http.get("/api/v1/evaluations").json()["evaluations"] == []
        assert http.get("/api/v1/approvals").json()["pendingCount"] == 0
        for artifact in session["artifacts"]:
            assert http.get(artifact["url"]).status_code == 200
        assert http.get(f"/api/v1/runtime-sessions/{session['id']}/artifacts/config").status_code == 404
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/v1/runtime-sessions").json()["sessions"] == [session]
        assert (
            restarted.get(f"/api/v1/runtime-sessions/{session['id']}").json()["usage"]["llmUsd"] == 0.3244434
        )
