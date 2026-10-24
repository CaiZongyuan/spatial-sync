"""Unavailable physical observations are infrastructure failures, not scored negatives."""

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.sources.synthetic_source import SyntheticSource
from tests.gateway.test_external_agent_evaluation import BASE, CENTRIFUGE, create, work
from tests.gateway.test_runtime_records import FIXTURE


def faulty_source(mode):
    class EvidenceSource(SyntheticSource):
        def entity_snapshot(self):
            snapshot = super().entity_snapshot().model_copy(deep=True)
            entity = next(entity for entity in snapshot.entities if entity.key == CENTRIFUGE)
            if mode == "missing":
                entity.values.pop("lidOpen")
            elif mode == "stale":
                entity.meta["lidOpen"] = entity.meta["lidOpen"].model_copy(update={"stale": True})
            elif mode == "missing-meta":
                entity.meta.pop("lidOpen")
            elif mode == "scope":
                snapshot = snapshot.model_copy(update={"epoch": snapshot.epoch + 1})
            elif mode == "invalid-type":
                entity.values["lidOpen"] = "not-a-physical-boolean"
            return snapshot

    return lambda kind, session_id: EvidenceSource(session_id)


@pytest.mark.parametrize("mode", ["missing", "stale", "missing-meta", "scope", "invalid-type", "fresh-false"])
def test_required_physical_evidence_is_invalid_while_fresh_false_stays_scored(mode):
    with TestClient(create_app(source_factory=faulty_source(mode))) as http:
        declaration = {
            "name": "claude-code",
            "version": "2.1.306",
            "model": "claude-opus-5-5",
            "thinking": "high",
        }
        run, _ = create(http, runtime=declaration)
        item = work(http, run)
        native = http.post(
            "/api/v1/runtime-sessions/import",
            json={
                "format": "claude-code-stream-json",
                "content": FIXTURE.read_text(encoding="utf-8"),
            },
        ).json()["session"]
        response = http.post(
            BASE + run["id"] + "/complete",
            json={
                "caseRunId": item["caseRunId"],
                "nativeSessionId": native["nativeSessionId"],
                "runtimeRecordId": native["id"],
            },
        )
        assert response.status_code == 200, response.text
        case = response.json()["case"]
        if mode == "fresh-false":
            assert case["status"] == "completed" and 0 <= case["score"] < 100
        else:
            assert case["status"] == "invalid", case
            assert "score" not in case and case["error"]["code"] == "agent_evidence_unavailable"
            assert case["runtimeRecordId"] == native["id"] and case["usage"]["llmUsd"] == 0.3244434
            assert {artifact["id"] for artifact in case["artifacts"]} == {"trace", "final"}
            for artifact in case["artifacts"]:
                assert http.get(artifact["url"]).status_code == 200
        assert response.json()["run"]["cleanup"]["complete"]
        assert http.get("/api/v1/suites/lab-ops-basic/1/scoreboard").json()["rows"] == []


def test_one_unavailable_required_case_stops_complete_train_publication():
    with TestClient(create_app(source_factory=faulty_source("missing"))) as http:
        run, _ = create(http, purpose="train", caseId=None)
        item = work(http, run)
        response = http.post(
            BASE + run["id"] + "/complete",
            json={
                "caseRunId": item["caseRunId"],
                "nativeSessionId": "unavailable-first-train-case",
            },
        )
        assert response.status_code == 200
        final = response.json()["run"]
        assert final["status"] == "failed" and final.get("result") is None
        assert len(final["cases"]) == 30 and final["cases"][0]["status"] == "invalid"
        assert len(http.get("/api/v1/health").json()["sessions"]) == 1
        assert http.post(BASE + run["id"] + "/next").json()["status"] == "finished"
        assert http.get("/api/v1/suites/lab-ops-basic/1/scoreboard").json()["rows"] == []
