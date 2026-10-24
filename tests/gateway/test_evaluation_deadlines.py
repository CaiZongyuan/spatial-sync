"""A healthy task deadline stays in the fixed evaluation denominator."""

import shutil
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.library.common import BUILTIN_ROOT


def deadline_suite(root):
    directory = root / "suites/healthy-deadline/1"
    shutil.copytree(BUILTIN_ROOT / "suites/lab-demo-pick-place/1", directory)
    metadata = yaml.safe_load((directory / "suite.yaml").read_text(encoding="utf-8"))
    metadata["id"] = "suite/healthy-deadline"
    task = yaml.safe_load((directory / "task.yaml").read_text(encoding="utf-8"))
    task["timeoutSeconds"] = 0.03
    (directory / "suite.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    (directory / "task.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    return "suite/healthy-deadline@1"


def finished(http, identifier):
    for _ in range(300):
        value = http.get("/api/v1/evaluations/" + identifier).json()
        if value["status"] in ("completed", "failed", "cancelled"):
            return value
        time.sleep(0.01)
    raise AssertionError(value)


def test_healthy_real_synthetic_deadlines_keep_both_failures_and_continue_fixed_protocol(
    tmp_path, monkeypatch
):
    root = tmp_path / "library"
    monkeypatch.setenv("LW_LIBRARY", str(root))
    suite = deadline_suite(root)
    with TestClient(create_app()) as http:
        response = http.post("/api/v1/evaluations", json={"subject": "robot_arm.virtual", "suite": suite})
        assert response.status_code == 202, response.text
        result = finished(http, response.json()["evaluationId"])
        assert result["status"] == "completed", result.get("error")
        assert result["result"]["successes"] == 0 and result["result"]["episodes"] == 2
        assert result["result"]["score"] == 0
        assert [episode["caseId"] for episode in result["episodes"]] == ["train-one", "train-two"]
        assert all(
            not episode["success"] and episode["reason"] == "task_timeout" for episode in result["episodes"]
        )
        assert len(set(result["result"]["recordings"])) == 2
        for episode in result["episodes"]:
            assert (
                http.get("/recordings/" + episode["recordingId"] + "/recording.json").json()["status"]
                == "complete"
            )


@pytest.mark.parametrize(
    "mode", ["missing", "stale", "missing-default", "stale-default", "scope", "source-crash"]
)
def test_unavailable_source_is_invalid_instead_of_a_healthy_task_timeout(tmp_path, monkeypatch, mode):
    root = tmp_path / "library"
    monkeypatch.setenv("LW_LIBRARY", str(root))
    suite = deadline_suite(root)

    class FaultSource(SyntheticSource):
        def entity_snapshot(self):
            snapshot = super().entity_snapshot().model_copy(deep=True)
            entity = next(entity for entity in snapshot.entities if entity.key == "/World/Lab/Tubes/Tube01")
            if mode == "missing":
                entity.values.pop("location")
            elif mode == "stale":
                entity.meta["location"] = entity.meta["location"].model_copy(update={"stale": True})
            elif mode == "scope":
                snapshot = snapshot.model_copy(update={"epoch": snapshot.epoch + 1})
            elif mode in ("missing-default", "stale-default"):
                robot = next(entity for entity in snapshot.entities if entity.key == "/World/Lab/RobotA")
                if mode == "missing-default":
                    robot.values.pop("holding")
                else:
                    robot.meta["holding"] = robot.meta["holding"].model_copy(update={"stale": True})
            return snapshot

        async def invoke(self, request):
            if mode == "source-crash":
                raise OSError("Controlled unavailable Source transport")
            return await super().invoke(request)

    with TestClient(create_app(source_factory=lambda kind, session_id: FaultSource(session_id))) as http:
        response = http.post("/api/v1/evaluations", json={"subject": "robot_arm.virtual", "suite": suite})
        assert response.status_code == 202
        result = finished(http, response.json()["evaluationId"])
        assert result["status"] == "failed" and result.get("result") is None
        assert result["error"]["code"] == (
            "source_unavailable" if mode == "source-crash" else "evaluation_observation_unavailable"
        )
        assert http.get("/api/v1/suites/healthy-deadline/1/scoreboard").json()["rows"] == []


def test_healthy_program_rejections_remain_valid_failures_without_timeout_reason(tmp_path, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setenv("LW_LIBRARY", str(root))
    suite = deadline_suite(root)
    path = root / "suites/healthy-deadline/1/suite.yaml"
    metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
    for episode in metadata["episodes"]:
        episode["params"] = {"from": "centrifuge.slot1", "to": "rack.A1"}
    path.write_text(yaml.safe_dump(metadata), encoding="utf-8")
    with TestClient(create_app()) as http:
        response = http.post("/api/v1/evaluations", json={"subject": "robot_arm.virtual", "suite": suite})
        assert response.status_code == 202
        result = finished(http, response.json()["evaluationId"])
        assert result["status"] == "completed" and result["result"]["episodes"] == 2
        assert result["result"]["successes"] == 0
        assert all(
            episode["invocationStatus"] == "rejected" and "reason" not in episode
            for episode in result["episodes"]
        )


def test_source_that_never_starts_does_not_create_a_valid_timeout_score(tmp_path, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setenv("LW_LIBRARY", str(root))
    suite = deadline_suite(root)

    def unavailable(kind, session_id):
        raise OSError("Controlled Source startup failure")

    with TestClient(create_app(source_factory=unavailable)) as http:
        response = http.post("/api/v1/evaluations", json={"subject": "robot_arm.virtual", "suite": suite})
        assert response.status_code == 202
        result = finished(http, response.json()["evaluationId"])
        assert result["status"] == "failed" and result.get("result") is None
        assert result["episodes"] == []
        assert http.get("/api/v1/suites/healthy-deadline/1/scoreboard").json()["rows"] == []
