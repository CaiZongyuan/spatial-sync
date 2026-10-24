"""Public Job sockets preserve canonical/env/reset identity and cannot acquire Session ownership."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_training_jobs import HEARTBEAT, NODE, TOKEN, approve, heartbeat, setup_plan

from apps.api.app.main import create_app
from apps.api.app.settings import Settings


def start(client):
    plan, task = setup_plan(client)
    approve(client, plan["approvalId"])
    client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task))
    job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
    report = {"jobId": job["jobId"], "leaseId": job["leaseId"], "status": "running"}
    assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report])).status_code == 200
    headers = NODE | {"X-Lease-Id": job["leaseId"]}
    inputs = client.get(f"/api/v1/jobs/{job['jobId']}/inputs", headers=headers)
    assert inputs.status_code == 200, inputs.text
    return plan, task, job, report, headers, inputs.json()["view"]


def hello(job, hint, view="view-fixture"):
    assert "error" not in hint, hint
    return {
        "schema": "lw.training-view/1",
        "type": "hello",
        "jobId": job["jobId"],
        "attemptId": hint["attemptId"],
        "viewId": view,
        "payload": {
            "task": job["task"],
            "taskContentHash": job["taskContentHash"],
            "scene": job["scene"],
            "sceneContentHash": job["sceneContentHash"],
            "assets": job["assets"],
            "descriptorDigest": hint["descriptorDigest"],
            "mappingDigest": "1" * 64,
            "environments": [0, 1, 2, 3],
            "source": {
                "kind": "newton",
                "newtonVersion": "fixture-only",
                "solver": "controlled GPU-free transport fixture",
                "physicsDt": 1 / 60,
                "basePublishHz": 10.0,
                "realtimeFactor": None,
                "sampleTimeClock": "runner-monotonic",
            },
            "maxRateHz": 10.0,
        },
    }


def frame(binding, hint, index, seq=0, epoch=0, offset=0):
    return {key: binding[key] for key in ("schema", "jobId", "attemptId", "viewId")} | {
        "type": "frame",
        "envIndex": index,
        "payload": {
            "protocolVersion": 2,
            "type": "stream.snapshot",
            "sessionId": f"training-view:{binding['viewId']}:env:{index}",
            "sceneRevision": "training-scene-" + hint["descriptorDigest"],
            "epoch": epoch,
            "seq": seq,
            "simTime": float(seq),
            "sampleTimeMs": 1000.0 + seq * 100,
            "poses": [
                [row["streamId"], offset + index + number * 0.01, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
                for number, row in enumerate(hint["bodyTable"])
            ],
        },
    }


def receive(socket, kind, *, index=None):
    for _ in range(32):
        value = socket.receive_json()
        if value["type"] == kind and (index is None or value.get("envIndex") == index):
            return value
    raise AssertionError(f"Expected {kind} env{index}")


def test_terminal_during_slow_reader_send_does_not_admit_the_remaining_old_frame_batch():
    app = create_app(Settings(node_token=TOKEN))
    entered, release = threading.Event(), threading.Event()
    views = app.state.training_views
    send = views.send

    async def blocked(websocket, message):
        if message["type"] == "frame" and message["envIndex"] == 0:
            entered.set()
            await asyncio.to_thread(release.wait, 3)
        await send(websocket, message)

    views.send = blocked
    with TestClient(app) as client:
        plan, task, job, report, headers, hint = start(client)
        identity = job["jobId"]
        with client.websocket_connect(f"/api/v1/jobs/{identity}/view-frames", headers=headers) as sender:
            binding = hello(job, hint)
            sender.send_json(binding)
            for index in range(4):
                sender.send_json(frame(binding, hint, index))
            deadline = time.monotonic() + 2
            while len(views.states.get(identity).latest if identity in views.states else {}) != 4:
                assert time.monotonic() < deadline
                time.sleep(0.005)
            with client.websocket_connect(f"/api/v1/jobs/{identity}/view") as viewer:
                assert entered.wait(2)
                report["status"] = "cancelled"
                terminal = client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report]))
                assert terminal.status_code == 200
                release.set()
                delivered = []
                while True:
                    try:
                        message = viewer.receive_json()
                        delivered.append(message)
                    except WebSocketDisconnect as closed:
                        assert closed.code == 1000
                        break
                # Env0's send was already admitted before terminal; no next old env may start.
                assert [message["envIndex"] for message in delivered if message["type"] == "frame"] == [0]
                assert delivered[-1]["type"] == "ended"


def test_competing_public_hello_cannot_revive_a_superseded_descriptor_waiter():
    app = create_app(Settings(node_token=TOKEN))
    views = app.state.training_views
    entered, release = threading.Event(), threading.Event()
    with TestClient(app) as client:
        _, _, job, _, headers, hint = start(client)
        descriptor, calls = views._descriptor, []

        async def blocked_descriptor(value):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                await asyncio.to_thread(release.wait, 3)
            return await descriptor(value)

        views._descriptor = blocked_descriptor
        url = f"/api/v1/jobs/{job['jobId']}/view"
        with (
            client.websocket_connect(url) as viewer,
            client.websocket_connect(url + "-frames", headers=headers) as old,
            client.websocket_connect(url + "-frames", headers=headers) as new,
        ):
            old.send_json(hello(job, hint, "old-descriptor-waiter"))
            assert entered.wait(2)
            new.send_json(hello(job, hint, "new-producer"))
            assert receive(viewer, "hello")["viewId"] == "new-producer"
            release.set()
            assert receive(old, "error")["payload"]["code"] == "training_view_stale_producer"
            assert views.states[job["jobId"]].hello.viewId == "new-producer"


@pytest.mark.parametrize("field,code", [
    ("attemptId", "training_view_identity_mismatch"),
    ("taskContentHash", "training_view_inputs_mismatch"),
    ("sceneContentHash", "training_view_inputs_mismatch"),
    ("descriptorDigest", "training_view_descriptor_mismatch"),
])
def test_public_producer_rejects_wrong_dispatch_pins_and_descriptor(field, code):
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        _, _, job, _, headers, hint = start(client)
        binding = hello(job, hint)
        if field == "attemptId":
            binding[field] = "other-attempt"
        else:
            binding["payload"][field] = "f" * 64
        with client.websocket_connect(f"/api/v1/jobs/{job['jobId']}/view-frames", headers=headers) as sender:
            sender.send_json(binding)
            assert receive(sender, "error")["payload"]["code"] == code
        assert client.get(f"/api/v1/jobs/{job['jobId']}").json()["status"] == "running"
        assert not app.state.training_views.states


def test_remote_reader_origin_role_and_node_lease_authorization_are_independent():
    origin = "https://owned.example"
    app = create_app(Settings(
        node_token=TOKEN, remote=True, operator_token="operator", viewer_token="viewer",
        allowed_origins=(origin,), remote_requests_per_minute=1000,
    ))
    with TestClient(app, headers={"Authorization": "Bearer operator"}) as client:
        _, _, job, _, headers, hint = start(client)
        url = f"/api/v1/jobs/{job['jobId']}/view"
        for bad in (
            {"Authorization": "Bearer viewer"},
            {"Authorization": "Bearer viewer", "Origin": "https://unowned.example"},
            {"Authorization": "Bearer unknown", "Origin": origin},
        ):
            with pytest.raises(WebSocketDisconnect) as rejected:
                with client.websocket_connect(url, headers=bad):
                    pass
            assert rejected.value.code == 1008
        viewer_headers = {"Authorization": "Bearer viewer", "Origin": origin}
        with client.websocket_connect(url, headers=viewer_headers) as viewer:
            assert receive(viewer, "status")["payload"]["state"] == "waiting"
            for field in ("X-Node-Token", "X-Node-Instance", "X-Lease-Id"):
                with pytest.raises(WebSocketDisconnect) as rejected:
                    with client.websocket_connect(
                        url + "-frames", headers=headers | {field: "wrong"}
                    ):
                        pass
                assert rejected.value.code == 1008
            with client.websocket_connect(url + "-frames", headers=headers) as sender:
                binding = hello(job, hint)
                sender.send_json(binding)
                assert receive(viewer, "hello")["viewId"] == binding["viewId"]
                sender.send_json(frame(binding, hint, 0))
                assert receive(viewer, "frame")["envIndex"] == 0


@pytest.mark.parametrize("tamper", ["resource", "provenance", "scope"])
def test_frozen_job_descriptor_detects_tamper_after_memory_cache_loss(tamper):
    import json

    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        _, _, job, _, headers, _ = start(client)
        descriptors = app.state.training_views.descriptors
        descriptor = descriptors._cache.pop(job["jobId"])
        record = descriptor.directory / "descriptor.json"
        value = json.loads(record.read_text())
        if tamper == "resource":
            (descriptor.directory / next(iter(value["resources"]))).write_bytes(b"changed")
        elif tamper == "provenance":
            value["source"]["newtonVersion"] = "invented"
            record.write_text(json.dumps(value))
        else:
            value["scope"]["taskContentHash"] = "f" * 64
            record.write_text(json.dumps(value))
        inputs = client.get(f"/api/v1/jobs/{job['jobId']}/inputs", headers=headers)
        assert inputs.status_code == 200
        assert inputs.json()["view"]["error"]["code"] == "training_view_manifest_unavailable"
        assert client.get(f"/api/v1/jobs/{job['jobId']}").json()["status"] == "running"


def test_four_env_same_keys_reset_reconnect_new_incarnation_and_terminal_cleanup():
    def forbidden(*args):
        raise AssertionError("A Job view cannot instantiate a spatial Session")

    app = create_app(Settings(node_token=TOKEN), source_factory=forbidden)
    with TestClient(app) as client:
        recording_files = sorted(app.state.settings.recordings_root.rglob("*"))
        plan, task, job, report, headers, hint = start(client)
        identity = job["jobId"]
        assert len(hint["bodyTable"]) == 40 and len(hint["sites"]) == 33 and len(hint["joints"]) == 40
        binding = hello(job, hint)
        url = f"/api/v1/jobs/{identity}/view"
        producer_url = f"/api/v1/jobs/{identity}/view-frames"
        with (
            client.websocket_connect(url) as viewer,
            client.websocket_connect(producer_url, headers=headers) as sender,
        ):
            assert receive(viewer, "status")["payload"]["state"] == "waiting"
            sender.send_json(binding)
            first = receive(viewer, "hello")
            assert first["payload"]["capture"]["source"]["newtonVersion"] == "1.7.0"
            manifests = [receive(viewer, "manifest") for _ in range(4)]
            assert {value["envIndex"] for value in manifests} == {0, 1, 2, 3}
            assert all(value["payload"]["source"]["newtonVersion"] == "fixture-only" for value in manifests)
            for value in manifests:
                assert "/sessions/" not in str(value["payload"])
            resources = [
                visual["url"]
                for node in manifests[0]["payload"]["nodes"]
                if node["kind"] != "group"
                for visual in node["visuals"]
                if visual["kind"] != "primitive"
            ]
            assert resources and all(
                resource.startswith(f"/api/v1/jobs/{identity}/view-assets/") for resource in resources
            )
            assert client.get(resources[0]).status_code == 200
            for index in range(4):
                sender.send_json(frame(binding, hint, index))
            samples = [receive(viewer, "frame") for _ in range(4)]
            assert {sample["envIndex"] for sample in samples} == {0, 1, 2, 3}
            assert len({sample["payload"]["poses"][0][1] for sample in samples}) == 4
            assert {sample["payload"]["seq"] for sample in samples} == {0}
            sender.send_json(frame(binding, hint, 1, seq=1, epoch=1))
            reset = receive(viewer, "frame", index=1)
            assert reset["payload"]["seq"] == 1 and reset["payload"]["epoch"] == 1
            assert app.state.training_views.states[identity].latest[0]["payload"]["epoch"] == 0
            with client.websocket_connect(url) as restored:
                assert receive(restored, "hello")["viewId"] == binding["viewId"]
                cached = receive(restored, "frame", index=1)
                assert cached["payload"] == reset["payload"]
            with client.websocket_connect(producer_url, headers=headers) as replacement:
                next_binding = hello(job, hint, "view-replacement")
                replacement.send_json(next_binding)
                assert receive(viewer, "hello")["viewId"] == "view-replacement"
                replacement.send_json(frame(next_binding, hint, 0, offset=5))
                assert receive(viewer, "frame", index=0)["payload"]["seq"] == 0
                sender.send_json(frame(binding, hint, 0, seq=100))
                assert receive(sender, "error")["payload"]["code"] == "training_view_stale_producer"
                assert client.get(f"/api/v1/jobs/{identity}").json()["status"] == "running"
                report["status"] = "cancelled"
                client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report]))
                assert receive(viewer, "ended")["payload"]["status"] == "cancelled"
                with pytest.raises(WebSocketDisconnect) as closed:
                    viewer.receive_json()
                assert closed.value.code == 1000
        assert not app.state.manager.sessions
        assert sorted(app.state.settings.recordings_root.rglob("*")) == recording_files
        assert not app.state.training_views.states
        assert client.get(resources[0]).status_code == 404
        with client.websocket_connect(url) as terminal:
            assert terminal.receive_json()["payload"]["status"] == "cancelled"


def test_public_operator_cannot_produce_and_browser_input_never_cancels_training():
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        _, _, job, _, _, _ = start(client)
        identity = job["jobId"]
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect(f"/api/v1/jobs/{identity}/view-frames"):
                pass
        assert refused.value.code == 1008
        with client.websocket_connect(f"/api/v1/jobs/{identity}/view") as browser:
            receive(browser, "status")
            browser.send_json({"type": "action.request"})
            with pytest.raises(WebSocketDisconnect) as closed:
                browser.receive_json()
            assert closed.value.code == 1008
        current = client.get(f"/api/v1/jobs/{identity}").json()
        assert current["status"] == "running" and not current["cancelRequested"]
        assert client.get("/api/v1/nodes").json()["nodes"][0]["leases"]


def test_missing_descriptor_is_an_explicit_view_error_without_blocking_job_inputs(tmp_path):
    app = create_app(Settings(node_token=TOKEN))
    app.state.training_views.descriptors.previews.roots = (tmp_path / "no-capture",)
    with TestClient(app) as client:
        _, _, job, _, _, hint = start(client)
        assert hint["error"]["code"] == "training_view_manifest_unavailable"
        assert client.get(f"/api/v1/jobs/{job['jobId']}").json()["status"] == "running"
        assert not app.state.manager.sessions


def test_bounded_latest_sample_rate_does_not_queue_or_create_frame_history():
    app = create_app(Settings(node_token=TOKEN))
    app.state.training_views.clock = lambda: 100.0
    with TestClient(app) as client:
        _, _, job, _, headers, hint = start(client)
        binding = hello(job, hint)
        identity = job["jobId"]
        with (
            client.websocket_connect(f"/api/v1/jobs/{identity}/view") as viewer,
            client.websocket_connect(f"/api/v1/jobs/{identity}/view-frames", headers=headers) as sender,
        ):
            sender.send_json(binding)
            for seq in range(11):
                sender.send_json(frame(binding, hint, 0, seq=seq))
            latest = receive(viewer, "frame", index=0)
            while latest["payload"]["seq"] < 9:
                latest = receive(viewer, "frame", index=0)
            state = app.state.training_views.states[identity]
            assert len(state.latest) == 1 and state.latest[0]["payload"]["seq"] == 9
            assert len(state.rates[0]) == 10
            app.state.training_views.clock = lambda: 102.0
            sender.send_json(frame(binding, hint, 0, seq=11))
            assert receive(viewer, "frame", index=0)["payload"]["seq"] == 11
            assert not list(app.state.settings.state_root.rglob("view*.jsonl"))


def test_unavailable_mapping_is_view_only_and_cpu_session_remains_controllable():
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        _, _, job, _, headers, hint = start(client)
        created = client.post("/api/v1/sessions", json={"sceneId": "lab-demo", "source": "synthetic"})
        assert created.status_code == 201, created.text
        session = created.json()["sessionId"]
        identity = job["jobId"]
        with (
            client.websocket_connect(f"/api/v1/jobs/{identity}/view") as viewer,
            client.websocket_connect(f"/api/v1/jobs/{identity}/view-frames", headers=headers) as sender,
        ):
            sender.send_json(
                {
                    "schema": "lw.training-view/1",
                    "type": "error",
                    "jobId": identity,
                    "attemptId": hint["attemptId"],
                    "viewId": None,
                    "payload": {
                        "code": "training_view_mapping_unavailable",
                        "message": "Controlled missing-map fixture",
                    },
                }
            )
            assert receive(viewer, "error")["payload"]["code"] == "training_view_mapping_unavailable"
            command = f"/api/v1/sessions/{session}/commands"
            paused = client.post(command, json={"requestId": "pause", "command": "pause"}).json()
            assert paused["ack"]["status"] == "applied"
            stepped = client.post(
                command, json={"requestId": "step", "command": "step", "args": {"count": 1}}
            ).json()
            assert stepped["ack"]["status"] == "applied" and stepped["state"]["seq"] > paused["state"]["seq"]
            assert client.get(f"/api/v1/jobs/{identity}").json()["status"] == "running"
            assert len(app.state.manager.sessions) == 1
        client.delete(f"/api/v1/sessions/{session}")


def test_slow_descriptor_does_not_block_heartbeat_cancel_or_the_actual_reaper(monkeypatch):
    app = create_app(Settings(node_token=TOKEN))
    clock = [100.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as client:
        plan, task, job, report, headers, _ = start(client)
        entered, release = threading.Event(), threading.Event()
        original = app.state.training_views.descriptors.prepare

        def slow(snapshot):
            entered.set()
            assert release.wait(5)
            return original(snapshot)

        monkeypatch.setattr(app.state.training_views.descriptors, "prepare", slow)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(client.get, f"/api/v1/jobs/{job['jobId']}/inputs", headers=headers)
            try:
                assert entered.wait(2)
                for instant in range(100, 281, 30):
                    clock[0] = instant
                    replied = pool.submit(
                        client.post, HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report])
                    ).result(timeout=0.5)
                    assert replied.status_code == 200 and not replied.json()["assignments"][0]["cancel"]
                    time.sleep(0.17)
                cancel = pool.submit(client.post, f"/api/v1/jobs/{job['jobId']}/cancel").result(timeout=0.5)
                assert cancel.status_code == 200 and cancel.json()["cancelRequested"]
            finally:
                release.set()
                assert pending.result(timeout=5).status_code == 200
        report["status"] = "cancelled"
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report])).status_code == 200
