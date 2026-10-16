"""Public WS scope negotiation, mixed wire variants, and real pump FIFO barriers."""

import asyncio
import json
import math
import struct

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.stream_broadcaster import ClientConnection
from packages.protocol.internal import SessionOptions
from packages.protocol.schema import Command, Subscribe


def packet(ws):
    value = ws.receive()
    if value.get("bytes") is None:
        return json.loads(value["text"])
    data = value["bytes"]
    kind = struct.unpack_from("<H", data, 6)[0]
    count = struct.unpack_from("<I", data, 40)[0]
    offset = 60 if kind == 3 else 48 if kind >= 4 else 44
    result = {
        "type": "transform.delta"
        if kind == 3
        else "stream.snapshot"
        if kind in (2, 5)
        else "transform.frame",
        "seq": struct.unpack_from("<Q", data, 16)[0],
        "epoch": struct.unpack_from("<I", data, 12)[0],
        "simTime": struct.unpack_from("<d", data, 24)[0],
        "poses": [struct.unpack_from("<I7f", data, offset + i * 32) for i in range(count)],
        "kind": kind,
    }
    if kind >= 3:
        result["subscriptionId"] = struct.unpack_from("<I", data, 44)[0]
    if kind == 3:
        result["baseSeq"], removed = struct.unpack_from("<QI", data, 48)
        result["removedStreamIds"] = list(struct.unpack_from(f"<{removed}I", data, offset + count * 32))
    return result


def until(ws, predicate):
    seen = []
    for _ in range(150):
        value = packet(ws)
        seen.append(value)
        if predicate(value):
            return seen
    raise AssertionError(seen)


def send_scope(ws, revision, **options):
    ws.send_json({"type": "client.subscribe", "sceneRevision": revision, "scope": "all", **options})
    ack = until(ws, lambda x: x["type"] == "subscription.ack")[-1]
    snapshot = until(ws, lambda x: x["type"] == "stream.snapshot")[-1]
    return ack, snapshot


@pytest.mark.parametrize("format_name", ["json", "binary"])
def test_legacy_and_scoped_first_subscription_never_share_the_wrong_wire_variant(format_name):
    with TestClient(create_app()) as http:
        summary = http.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid, revision = summary["sessionId"], summary["sceneRevision"]
        with (
            http.websocket_connect(f"/api/v1/sessions/{sid}/ws") as legacy,
            http.websocket_connect(f"/api/v1/sessions/{sid}/ws") as scoped,
        ):
            packet(legacy)
            packet(scoped)
            send_scope(legacy, revision, format=format_name)
            send_scope(scoped, revision, format=format_name, requestId="scoped-1")
            legacy.send_json({"type": "command.request", "requestId": "pause", "command": "pause"})

            def stop(x):
                return x["type"] == "session.state" and x["cause"].get("requestId") == "pause"

            first, second = until(legacy, stop), until(scoped, stop)
            a, b = first[-3], second[-2]
            assert [x["type"] for x in first[-3:]] == ["transform.frame", "command.ack", "session.state"]
            assert [x["type"] for x in second[-2:]] == ["transform.frame", "session.state"]
            assert "subscriptionId" not in a and b["subscriptionId"] == 1
            assert a["seq"] == b["seq"]
            assert a["poses"] == b["poses"]
            if format_name == "binary":
                assert (a["kind"], b["kind"]) == (1, 4)


@pytest.mark.parametrize("format_name", ["json", "binary"])
def test_scopes_delta_keyframes_commands_rebase_and_reconnect(format_name):
    with TestClient(create_app()) as http:
        summary = http.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid, revision = summary["sessionId"], summary["sceneRevision"]
        route = f"/api/v1/sessions/{sid}"
        with http.websocket_connect(route + "/ws") as ws:
            packet(ws)
            ack, snapshot = send_scope(
                ws,
                revision,
                format=format_name,
                requestId="robot",
                scope="device",
                deviceKey="/World/Lab/Robot",
                delta=True,
            )
            assert ack["requestId"] == "robot" and ack["delta"]
            assert {row[0] for row in snapshot["poses"]} == {1, 2}
            delta = until(ws, lambda x: x["type"] == "transform.delta")[-1]
            assert delta["baseSeq"] == snapshot["seq"]
            # Regular full keyframes reliably rebase a running sparse stream.
            keyframe = until(ws, lambda x: x["type"] == "transform.frame")[-1]
            following = until(ws, lambda x: x["type"] == "transform.delta")[-1]
            assert following["baseSeq"] == keyframe["seq"]
            for command in ("pause", "step", "reset", "resume"):
                ws.send_json({"type": "command.request", "requestId": command, "command": command})
                trace = until(
                    ws, lambda x: x["type"] == "session.state" and x["cause"].get("requestId") == command
                )
                frame, command_ack, state = trace[-3:]
                assert frame["type"] == ("stream.snapshot" if command == "reset" else "transform.frame")
                assert command_ack["type"] == "command.ack" and state["seq"] == frame["seq"]
                assert {row[0] for row in frame["poses"]} == {1, 2}
            following = until(ws, lambda x: x["type"] == "transform.delta")[-1]
            assert following["baseSeq"] == frame["seq"]
            # Joint selection includes both parent and child; a Site includes its Body.
            _, joint = send_scope(
                ws,
                revision,
                format=format_name,
                requestId="joint",
                scope="entities",
                entityKeys=["/World/Lab/Robot/Joint2"],
                delta=True,
            )
            assert {row[0] for row in joint["poses"]} == {1, 2}
            scene = http.get(route + "/scene").json()
            site = next(x for x in scene["sites"] if x["parentBodyKey"] == "/World/Lab/Robot/Link2")
            _, site_frame = send_scope(
                ws,
                revision,
                format=format_name,
                requestId="site",
                scope="entities",
                entityKeys=[site["entityKey"]],
                delta=True,
            )
            assert {row[0] for row in site_frame["poses"]} == {2}
            assert len(http.get(route + "/snapshot").json()["poses"]) == 24
        with http.websocket_connect(route + "/ws") as ws:
            packet(ws)
            _, recovered = send_scope(ws, revision, format=format_name, requestId="reconnect", delta=True)
            assert len(recovered["poses"]) == 24 and recovered["seq"] >= site_frame["seq"]


async def test_blocked_real_pump_preserves_other_clients_command_before_rapid_scope_barriers():
    class Socket:
        def __init__(self):
            self.gate, self.started = asyncio.Event(), asyncio.Event()
            self.messages = []

        async def send_text(self, payload):
            self.started.set()
            await self.gate.wait()
            self.messages.append(json.loads(payload))

        async def close(self, code):
            pass

    app = create_app(Settings(send_timeout_seconds=2))
    async with app.router.lifespan_context(app):
        session = await app.state.manager.create("lab-demo", "synthetic", SessionOptions())
        socket = Socket()
        observer = ClientConnection(socket, session.settings)
        commander = ClientConnection(None, session.settings)
        session.clients.update((observer, commander))
        observer.start()
        revision = session.manifest.sceneRevision
        await session.observer_subscribe(
            observer, Subscribe(sceneRevision=revision, requestId="first", delta=True)
        )
        await asyncio.wait_for(socket.started.wait(), 1)
        await session.execute(commander, Command(requestId="pause-other", command="pause"))
        await session.observer_subscribe(
            observer,
            Subscribe(
                sceneRevision=revision,
                requestId="second",
                scope="entities",
                entityKeys=["/World/Lab/Robot/Link1"],
                delta=True,
            ),
        )
        await session.observer_subscribe(
            observer,
            Subscribe(
                sceneRevision=revision,
                requestId="third",
                scope="device",
                deviceKey="/World/Lab/Tubes",
                delta=True,
            ),
        )
        socket.gate.set()
        for _ in range(200):
            if sum(m["type"] == "stream.snapshot" for m in socket.messages) == 3:
                break
            await asyncio.sleep(0.001)
        trace = socket.messages
        state_index = next(
            i
            for i, m in enumerate(trace)
            if m["type"] == "session.state" and m["cause"].get("requestId") == "pause-other"
        )
        assert trace[state_index - 1]["type"] == "transform.frame"
        assert trace[state_index - 1]["subscriptionId"] == 1
        assert trace[state_index - 1]["seq"] == trace[state_index]["seq"]
        assert [m["requestId"] for m in trace if m["type"] == "subscription.ack"] == [
            "first",
            "second",
            "third",
        ]
        assert state_index < next(i for i, m in enumerate(trace) if m.get("requestId") == "second")
        assert [m["subscriptionId"] for m in trace if m["type"] == "stream.snapshot"] == [1, 2, 3]
        await observer.finish()
        session.clients.discard(commander)


@pytest.mark.parametrize("format_name", ["json", "binary"])
def test_real_moving_source_viewport_membership_and_dropped_deltas_use_the_declared_base(format_name):
    with TestClient(create_app()) as http:
        summary = http.post("/api/v1/sessions", json={"sceneId": "lab-demo", "realtimeFactor": 4}).json()
        route = f"/api/v1/sessions/{summary['sessionId']}/ws"
        # Physics Z-up lower plane; tube origins alone lie outside this bound.
        planes = [
            [1, 0, 0, 10],
            [-1, 0, 0, 10],
            [0, 1, 0, 10],
            [0, -1, 0, 10],
            [0, 0, 1, -1.045],
            [0, 0, -1, 10],
        ]
        with http.websocket_connect(route) as ws:
            packet(ws)
            _, initial = send_scope(
                ws,
                summary["sceneRevision"],
                scope="viewport",
                viewport={"planes": planes},
                requestId="moving",
                format=format_name,
                delta=True,
            )
            baseline = {row[0]: row for row in initial["poses"]}
            base_seq = initial["seq"]
            initial_ids = set(baseline)
            changed, skipped, accepted = False, 0, 0
            for index in range(45):
                frame = packet(ws)
                if frame["type"] not in ("transform.frame", "transform.delta"):
                    continue
                if frame["type"] == "transform.frame":
                    baseline = {row[0]: row for row in frame["poses"]}
                    base_seq = frame["seq"]
                    continue
                # Deliberately do not consume alternate periodic deltas.
                if index % 2 == 0:
                    skipped += 1
                    continue
                assert frame["baseSeq"] == base_seq
                reconstructed = dict(baseline)
                for key in frame["removedStreamIds"]:
                    reconstructed.pop(key, None)
                reconstructed.update({row[0]: row for row in frame["poses"]})
                radius = math.hypot(0.025, 0.1)
                expected = {
                    10 + i
                    for i in range(20)
                    if 0.91 + 0.06 * math.sin(frame["simTime"] * 1.2 + i * 0.4) + radius >= 1.045
                }
                assert set(reconstructed) & set(range(10, 30)) == expected
                assert 31 in reconstructed  # Unknown GLB bounds conservatively stay included.
                changed |= set(reconstructed) != initial_ids
                accepted += 1
                if changed and accepted >= 5:
                    break
            assert changed and accepted >= 5 and skipped >= 1
