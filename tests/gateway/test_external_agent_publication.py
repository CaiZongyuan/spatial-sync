"""A public work grant follows its durable running record, including zero-tool Targets."""

from threading import Event

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.info import evaluations as evaluation_records
from tests.gateway.test_external_agent_evaluation import BASE, create, work


def test_next_work_is_withheld_until_the_running_record_is_durable(monkeypatch):
    entered, release = Event(), Event()
    write = evaluation_records.durable_json

    def held_filesystem_write(path, value):
        # Controlled filesystem boundary, not a mock of evaluator decisions.
        if (
            value.get("layer") == "agent"
            and any(case["status"] == "running" for case in value["cases"])
            and not entered.is_set()
        ):
            entered.set()
            assert release.wait(10), "The test must release its owned filesystem gate"
        return write(path, value)

    monkeypatch.setattr(evaluation_records, "durable_json", held_filesystem_write)
    with TestClient(create_app()) as http:
        run, _ = create(http)
        try:
            assert entered.wait(10)
            before_commit = http.post(BASE + run["id"] + "/next")
            assert before_commit.status_code == 200
            assert before_commit.json()["status"] == "pending"
            release.set()
            issued = work(http, run)
            assert issued["caseRunId"] == run["cases"][0]["caseRunId"]
        finally:
            release.set()
            http.post(BASE + run["id"] + "/cancel").raise_for_status()


def test_immediate_zero_tool_completion_retains_three_independent_valid_negative_attempts():
    with TestClient(create_app()) as http:
        run, _ = create(http, purpose="train", caseId=None)
        outcomes = []
        try:
            for _ in range(3):
                item = work(http, run)
                response = http.post(
                    BASE + run["id"] + "/complete",
                    json={
                        "caseRunId": item["caseRunId"],
                        "nativeSessionId": "zero-tool-" + item["caseRunId"],
                    },
                )
                assert response.status_code == 200
                outcome = response.json()["case"]
                assert outcome["status"] == "completed", outcome.get("error")
                assert outcome["metrics"]["steps"] == 0 and 0 <= outcome["score"] < 100
                outcomes.append(outcome)
            assert len({case["recordingId"] for case in outcomes}) == 3
            retained = http.get(BASE + run["id"]).json()
            assert all(case["status"] == "completed" for case in retained["cases"][:3])
            assert retained.get("result") is None
        finally:
            http.post(BASE + run["id"] + "/cancel").raise_for_status()
