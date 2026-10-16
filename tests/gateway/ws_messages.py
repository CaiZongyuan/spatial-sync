"""Decode the public mixed control/binary WebSocket boundary in acceptance tests."""

import json

from packages.transport import FrameCodec


def receive_message(ws):
    packet = ws.receive()
    if packet.get("bytes") is not None:
        return ws.frame_codec.decode(packet["bytes"]).model_dump(mode="python")
    value = json.loads(packet["text"])
    assert value["type"] not in ("transform.frame", "stream.snapshot", "transform.delta")
    if value["type"] in ("server.ready", "scene.replaced"):
        ws.frame_codec = FrameCodec(value["sessionId"], value["sceneRevision"], value["revisionNumericId"])
    return value
