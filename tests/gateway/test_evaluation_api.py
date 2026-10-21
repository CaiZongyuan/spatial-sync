import asyncio
import json
import shutil
import time

import yaml
from fastapi.testclient import TestClient
from ws_messages import receive_message

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.log import read_segment
from packages.info.store import StateStore
from packages.library.common import BUILTIN_ROOT
from packages.protocol.schema import EntityObservations, EntitySnapshot
from packages.transport import FrameCodec

SUITE = "suite/lab-demo-pick-place@1"


def eventually(read, predicate, timeout=120):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if predicate(value):
            return value
        assert time.monotonic() < deadline, value
        time.sleep(0.05)


def test_two_real_episodes_queue_and_private_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    source_starts = []

    def factory(kind, session_id):
        assert kind == "synthetic"
        source_starts.append(session_id)
        return SyntheticSource(session_id)

    app = create_app(
        Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings", recordings_keep=1),
        source_factory=factory,
    )
    with TestClient(app) as client:
        submitted = client.post("/api/v1/evaluations", json={"subject": "robot_arm.virtual", "suite": SUITE})
        assert submitted.status_code == 202, submitted.text
        identifier = submitted.json()["evaluationId"]
        health = eventually(
            lambda: client.get("/api/v1/health").json(),
            lambda value: any(row.get("evaluationId") == identifier for row in value["sessions"]),
        )
        session_id = next(
            row["sessionId"] for row in health["sessions"] if row.get("evaluationId") == identifier
        )
        route = "/api/v1/sessions/" + session_id
        assert "seed" not in client.get(route).json()
        with client.websocket_connect(route + "/ws", headers={"X-Actor": "user:platform-evaluator"}) as ws:
            assert receive_message(ws)["type"] == "server.ready"
            for message in (
                {"type": "command.request", "requestId": "ws-reset", "command": "reset"},
                {
                    "type": "action.request",
                    "requestId": "ws-home",
                    "entityKey": "/World/Lab/RobotA",
                    "action": "home",
                    "params": {},
                },
                {"type": "action.cancel", "requestId": "ws-cancel", "invocationId": "unknown"},
                {
                    "type": "action.approve",
                    "requestId": "ws-approve",
                    "invocationId": "unknown",
                    "decision": "approve",
                },
            ):
                ws.send_json(message)
                while (reply := receive_message(ws)).get("requestId") != message["requestId"]:
                    pass
                assert reply["type"] == "server.error" and reply["code"] == "invalid_message"
                assert reply["message"] == "evaluation_busy: This session is evaluator-owned and read-only"
            assert client.get(route).json()["state"] == "RUNNING"
        interference = client.post(
            route + "/actions",
            headers={"X-Actor": "user:platform-evaluator"},
            json={"requestId": "http-home", "entityKey": "/World/Lab/RobotA", "action": "home", "params": {}},
        )
        assert interference.status_code == 409
        plan = client.post("/api/v1/plans", json={"sessionId": session_id})
        assert plan.status_code == 409 and plan.json()["detail"]["code"] == "evaluation_busy"
        for suffix, method, body in (
            ("/commands", "post", {"requestId": "interrupt", "command": "reset"}),
            ("", "delete", None),
            ("/viewer", "post", None),
        ):
            response = getattr(client, method)(route + suffix, **({"json": body} if body else {}))
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "evaluation_busy"
        raw = eventually(
            lambda: client.get(f"/recordings/{session_id}/recording.json"),
            lambda value: value.status_code == 200,
        )
        assert "seed" not in raw.json()
        queued = client.post(
            "/api/v1/sessions", json={"sceneVersion": "lab-demo@3", "seed": 77, "requestId": "request-kept"}
        )
        cancelled = client.post(
            "/api/v1/sessions",
            json={"sceneVersion": "lab-demo@3", "seed": 78, "requestId": "request-cancelled"},
        )
        assert queued.status_code == cancelled.status_code == 202
        assert client.delete("/api/v1/session-requests/request-cancelled").json()["status"] == "cancelled"
        result = eventually(
            lambda: client.get("/api/v1/evaluations/" + identifier).json(),
            lambda value: value["status"] in ("completed", "failed"),
        )
        assert result["status"] == "completed", result
        assert result["result"]["successes"] == 2
        assert result["result"]["decision"] == "baseline"
        assert result["result"]["key"]["source"] == "synthetic"
        assert result["result"]["key"]["newtonVersion"] is None
        assert len(set(result["result"]["recordings"])) == 2
        owned = [
            session for session in app.state.manager.sessions.values() if session.evaluation_id == identifier
        ]
        assert [session.options.seed for session in owned] == [4101, 4102]
        for episode in result["episodes"]:
            metadata = client.get(f"/recordings/{episode['recordingId']}/recording.json").json()
            assert metadata["status"] == "complete" and "seed" not in metadata
            assert episode["milestones"] == {"grasp": True, "place": True}
            mirror = StateStore(next(iter(metadata["revisions"])))
            codec = FrameCodec(episode["recordingId"], mirror.scene_revision, 1)
            for segment in metadata["segments"]:
                response = client.get(f"/recordings/{episode['recordingId']}/{segment['file']}")
                assert response.status_code == 200
                decoded = read_segment(response.content)
                assert not decoded.truncated and decoded.valid_bytes == len(response.content)
                for record in decoded.records:
                    if record.kind == 1:
                        codec.decode(record.payload)
                    elif record.kind == 2:
                        document = json.loads(record.payload)
                        if document["type"] in ("entity.snapshot", "entity.observations"):
                            model = (
                                EntitySnapshot
                                if document["type"] == "entity.snapshot"
                                else EntityObservations
                            )
                            mirror.apply(model.model_validate(document))
            assert (
                mirror.values()["/World/Lab/Tubes/Tube01"]["location"] == "/World/Lab/Centrifuge/Rotor/Slot1"
            )
        started = eventually(
            lambda: client.get("/api/v1/session-requests/request-kept").json(),
            lambda value: value["status"] == "started",
        )
        interactive = client.get("/api/v1/sessions/" + started["sessionId"]).json()
        assert interactive["seed"] == 77 and "evaluationId" not in interactive
        previous_revision = client.get(route).json()["sceneRevision"]
        previous_metadata = client.get(f"/recordings/{session_id}/recording.json").content
        previous_starts = source_starts.copy()
        old_episode_reload = client.post(route + "/reload")
        assert old_episode_reload.status_code == 409
        assert old_episode_reload.json()["detail"]["code"] == "evaluation_busy"
        assert source_starts == previous_starts
        assert client.get(route).json()["sceneRevision"] == previous_revision
        assert client.get(f"/recordings/{session_id}/recording.json").content == previous_metadata
        active = [
            row
            for row in client.get("/api/v1/health").json()["sessions"]
            if row["state"] in ("RUNNING", "PAUSED", "LOADING")
        ]
        assert [row["sessionId"] for row in active] == [started["sessionId"]]
        assert client.delete("/api/v1/sessions/" + started["sessionId"]).status_code == 204
        assert client.get("/api/v1/session-requests/request-cancelled").json()["status"] == "cancelled"
        assert len(app.state.manager.sessions) == 3
        rows = client.get("/api/v1/suites/lab-demo-pick-place/1/scoreboard").json()["rows"]
        assert rows == [result["result"]]
        assert all(
            private not in json.dumps(result) for private in ("4101", "4102", "9101", "held-out-example")
        )


def test_public_test_execution_is_denied_for_forged_roles(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    with TestClient(
        create_app(Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"))
    ) as client:
        response = client.post(
            "/api/v1/evaluations",
            headers={"X-Role": "builder", "X-Actor": "user:builder"},
            json={"subject": "robot_arm.virtual", "suite": SUITE, "split": "test"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "test_requires_promotion"


def test_private_test_receipts_never_leave_any_public_evaluation_read_or_control(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    app = create_app(Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"))
    with TestClient(app) as client:
        service = app.state.manager.evaluations
        private = {
            "evaluationId": "eval-private-canary",
            "split": "test",
            "status": "completed",
            "privateQuestion": "private-canary-864209",
            "score": 0.25,
            "suite": SUITE,
        }
        service.store.save(private)
        service.store.rows.append(private | {"decision": "test"})
        for method, suffix in (("get", ""), ("post", "/cancel"), ("post", "/cleanup")):
            response = getattr(client, method)("/api/v1/evaluations/eval-private-canary" + suffix)
            assert response.status_code == 404
            assert "private-canary-864209" not in response.text
        assert client.get("/api/v1/evaluations").json() == {"evaluations": []}
        assert client.get("/api/v1/suites/lab-demo-pick-place/1/scoreboard").json() == {"rows": []}


def test_successful_action_can_fail_task_success(tmp_path, monkeypatch):
    library = tmp_path / "library"
    package = library / "suites" / "negative-success" / "1"
    shutil.copytree(BUILTIN_ROOT / "suites/lab-demo-pick-place/1", package)
    suite = yaml.safe_load((package / "suite.yaml").read_text(encoding="utf-8"))
    suite["id"] = "suite/negative-success"
    suite["episodes"] = suite["episodes"][:1]
    (package / "suite.yaml").write_text(yaml.safe_dump(suite), encoding="utf-8")
    task = yaml.safe_load((package / "task.yaml").read_text(encoding="utf-8"))
    task["success"]["all"][0]["equals"] = "/World/Lab/Centrifuge/Rotor/Slot2"
    (package / "task.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    (package / "private/seeds.yaml").write_text("train-one: 123456789\n", encoding="utf-8")
    monkeypatch.setenv("LW_LIBRARY", str(library))
    with TestClient(
        create_app(Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"))
    ) as client:
        submitted = client.post(
            "/api/v1/evaluations", json={"subject": "robot_arm.virtual", "suite": "suite/negative-success@1"}
        )
        assert submitted.status_code == 202, submitted.text
        result = eventually(
            lambda: client.get("/api/v1/evaluations/" + submitted.json()["evaluationId"]).json(),
            lambda value: value["status"] in ("completed", "failed"),
        )
        assert result["status"] == "completed", result
        assert result["episodes"][0]["invocationStatus"] == "succeeded"
        assert result["episodes"][0]["success"] is False
        assert result["result"]["successes"] == result["result"]["score"] == 0
        assert result["episodes"][0]["failedStage"] == "success"


def test_cancelling_a_starting_launch_closes_only_its_new_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    entered, release = asyncio.Event(), asyncio.Event()
    sources = []

    class WaitingSource(SyntheticSource):
        async def start(self, scene_id, options):
            entered.set()
            await release.wait()
            return await super().start(scene_id, options)

    def factory(kind, session_id):
        assert kind == "synthetic"
        source = SyntheticSource(session_id) if not sources else WaitingSource(session_id)
        sources.append(source)
        return source

    app = create_app(
        Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"),
        source_factory=factory,
    )
    with TestClient(app) as client:
        try:
            manual = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3"}).json()
            submitted = client.post(
                "/api/v1/evaluations", json={"subject": "robot_arm.virtual", "suite": SUITE}
            ).json()
            queued = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3", "seed": 66})
            assert queued.status_code == 202
            request_id = queued.json()["requestId"]
            client.post("/api/v1/evaluations/" + submitted["evaluationId"] + "/cancel").raise_for_status()
            assert client.delete("/api/v1/sessions/" + manual["sessionId"]).status_code == 204
            client.portal.call(asyncio.wait_for, entered.wait(), 2)
            assert client.get("/api/v1/session-requests/" + request_id).json()["status"] == "starting"
            cancelled = client.delete("/api/v1/session-requests/" + request_id)
            assert cancelled.json()["status"] == "cancelling"
            previous_revision = client.get("/api/v1/sessions/" + manual["sessionId"]).json()["sceneRevision"]
            previous_metadata = client.get(f"/recordings/{manual['sessionId']}/recording.json").content
            old_reload = client.post("/api/v1/sessions/" + manual["sessionId"] + "/reload")
            assert old_reload.status_code == 409 and old_reload.json()["detail"]["code"] == "evaluation_busy"
            assert len(sources) == 2
            assert (
                client.get("/api/v1/sessions/" + manual["sessionId"]).json()["sceneRevision"]
                == previous_revision
            )
            assert (
                client.get(f"/recordings/{manual['sessionId']}/recording.json").content == previous_metadata
            )
            client.portal.call(release.set)
            terminal = eventually(
                lambda: client.get("/api/v1/session-requests/" + request_id).json(),
                lambda value: value["status"] in ("cancelled", "failed"),
                timeout=5,
            )
            assert terminal["status"] == "cancelled", terminal
            assert terminal["sessionId"] != manual["sessionId"]
            assert client.get("/api/v1/sessions/" + terminal["sessionId"]).json()["state"] == "STOPPED"
            assert app.state.manager.get(terminal["sessionId"]).cleanup_complete
            assert len(sources) == 2
            assert all(
                value["state"] == "STOPPED" for value in client.get("/api/v1/health").json()["sessions"]
            )
        finally:
            client.portal.call(release.set)
