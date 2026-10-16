"""Information-plane messages are the shared Python/TypeScript public boundary."""

import pytest
from pydantic import TypeAdapter, ValidationError

from packages.protocol.schema import ClientMessage, InvocationUpdate, ServerMessage


def test_information_invocation_update_roundtrips_and_rejects_invalid_progress():
    message = {
        "type": "invocation.update",
        "sceneRevision": "r",
        "invocationId": "inv-1",
        "requestId": "request-1",
        "entityKey": "/World/Lab/Centrifuge",
        "action": "start",
        "params": {"rpm": 3000},
        "actor": {"kind": "agent", "name": "test"},
        "status": "running",
        "progress": 0.5,
        "epoch": 0,
        "createdSimTime": 0,
        "createdAtMs": 1234,
    }
    update = TypeAdapter(ServerMessage).validate_python(message)
    assert isinstance(update, InvocationUpdate) and update.actor.kind == "agent"
    assert update.progress == 0.5
    with pytest.raises(ValidationError):
        TypeAdapter(ServerMessage).validate_python(message | {"progress": 2})
    action = TypeAdapter(ClientMessage).validate_python(
        {
            "type": "action.request",
            "requestId": "web-1",
            "entityKey": "/World/Lab/Centrifuge",
            "action": "start",
            "params": {"rpm": 3000},
        }
    )
    assert action.params == {"rpm": 3000}
