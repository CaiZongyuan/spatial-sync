import json

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.transport import FrameCodec


def read_until(ws, codec, predicate):
    seen = []
    for _ in range(100):
        packet = ws.receive()
        value = (
            codec.decode(packet["bytes"]).model_dump(mode="json")
            if packet.get("bytes") is not None
            else json.loads(packet["text"])
        )
        if value["type"] in ("transform.frame", "stream.snapshot"):
            assert isinstance(packet.get("bytes"), bytes)
        seen.append(value)
        if predicate(value):
            return seen
    raise AssertionError("Expected stream response was not delivered")


def subscribe(ws, sid, revision, numeric):
    assert ws.receive_json()["type"] == "server.ready"
    ws.send_json(
        {
            "type": "client.subscribe",
            "protocolVersion": 2,
            "sceneRevision": revision,
            "rateHz": 30,
        }
    )
    assert ws.receive_json()["type"] == "subscription.ack"
    codec = FrameCodec(sid, revision, numeric)
    read_until(ws, codec, lambda m: m["type"] == "stream.snapshot")
    return codec


def test_binary_observers_share_reliable_final_frames_and_global_reload_metadata():
    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.03))) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid, revision = session["sessionId"], session["sceneRevision"]
        route = f"/api/v1/sessions/{sid}"
        with (
            client.websocket_connect(route + "/ws") as binary,
            client.websocket_connect(route + "/ws") as observer,
        ):
            codec = subscribe(binary, sid, revision, 1)
            subscribe(observer, sid, revision, 1)
            for command in ("pause", "step", "reset", "resume"):
                binary.send_json({"type": "command.request", "requestId": command, "command": command})

                def matches(message):
                    return message["type"] == "session.state" and message["cause"].get("requestId") == command

                first = read_until(binary, codec, matches)
                second = read_until(observer, codec, matches)
                ordered = [
                    m
                    for m in first
                    if m["type"] in ("transform.frame", "stream.snapshot", "command.ack", "session.state")
                ]
                assert [m["type"] for m in ordered[-3:]] == [
                    "stream.snapshot" if command == "reset" else "transform.frame",
                    "command.ack",
                    "session.state",
                ]
                frame = ordered[-3]
                other = [m for m in second if m["type"] == frame["type"]][-1]
                assert frame["seq"] == other["seq"] == ordered[-1]["seq"]
                for row, expected in zip(frame["poses"], other["poses"], strict=True):
                    assert row == pytest.approx(expected, abs=1e-6, rel=0)
            prior_seq = frame["seq"]
            reloaded = client.post(route + "/reload").json()
            read_until(binary, codec, lambda m: m["type"] == "scene.replaced")
            binary.send_json(
                {
                    "type": "client.subscribe",
                    "protocolVersion": 2,
                    "sceneRevision": reloaded["sceneRevision"],
                }
            )
            ack = read_until(binary, codec, lambda m: m["type"] == "subscription.ack")[-1]
            assert "format" not in ack
            payload = binary.receive_bytes()
            with pytest.raises(ValueError, match="revision"):
                codec.decode(payload)
            next_codec = FrameCodec(sid, reloaded["sceneRevision"], reloaded["revisionNumericId"])
            snapshot = next_codec.decode(payload)
            assert snapshot.type == "stream.snapshot" and snapshot.seq > prior_seq


async def test_binary_send_timeout_isolated_and_single_pump_orders_mixed_payloads():
    import asyncio

    from apps.api.app.stream_broadcaster import ClientConnection

    class Socket:
        def __init__(self, blocked=False):
            self.gate = asyncio.Event()
            if not blocked:
                self.gate.set()
            self.messages, self.close_codes = [], []
            self.writers, self.max_writers = 0, 0

        async def send_text(self, value):
            self.writers += 1
            self.max_writers = max(self.max_writers, self.writers)
            try:
                await self.gate.wait()
                self.messages.append(value)
            finally:
                self.writers -= 1

        send_bytes = send_text

        async def close(self, code):
            self.close_codes.append(code)

    settings = Settings(send_timeout_seconds=0.02)
    stalled, socket = Socket(blocked=True), Socket()
    slow, healthy = ClientConnection(stalled, settings), ClientConnection(socket, settings)
    slow.start()
    healthy.offer_frame(b"old", 1)
    healthy.enqueue_reliable(b"command-frame", seq=2)
    healthy.enqueue_reliable("ack")
    healthy.enqueue_reliable("state")
    healthy.start()
    slow.enqueue_reliable(b"blocked-binary")
    await asyncio.wait_for(slow.closed.wait(), timeout=0.5)
    assert stalled.close_codes == [1013]
    assert socket.messages == [b"command-frame", "ack", "state"]
    assert socket.max_writers == 1 and not healthy.closed.is_set()
    await healthy.finish()
