"""Generate reproducible JSON Schema and wire fixtures from authoritative Pydantic models."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from packages.protocol.schema import (  # noqa: E402
    Command, CommandAck, EntityState, Heartbeat, ProtocolContract, Ready,
    SceneManifest, SceneReplaced, ServerError, SessionState, Snapshot, Subscribe,
    SubscriptionAck, TransformFrame,
)


def draft7(value):
    """Pydantic 2020-12 fixed tuples use prefixItems; draft7's items is equivalent."""
    if isinstance(value, list):
        return [draft7(item) for item in value]
    if isinstance(value, dict):
        result = {("items" if key == "prefixItems" else key): draft7(item) for key, item in value.items() if key != "title"}
        if "required" in result:
            result["required"] = [name for name in result["required"] if result["properties"][name].get("default", False) is not None]
        return result
    return value


def write(path: str, value):
    target = ROOT / "packages/protocol" / path
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if "--check" in sys.argv:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            raise SystemExit(f"Generated artifact differs: {target.relative_to(ROOT)}; run pnpm protocol:generate")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def manifest_sample():
    identity = {"p": [0, 0, 0], "q": [0, 0, 0, 1]}
    geometries = [
        {"type": "box", "halfExtents": [0.2, 0.3, 0.4]},
        {"type": "sphere", "radius": 0.2},
        {"type": "capsule", "radius": 0.1, "halfHeight": 0.3},
        {"type": "cylinder", "radius": 0.1, "halfHeight": 0.3},
        {"type": "cone", "radius": 0.1, "halfHeight": 0.3},
        {"type": "plane", "width": 0, "length": 0},
    ]
    return SceneManifest.model_validate({
        "protocolVersion": 1, "sessionId": "session-fixture", "sceneRevision": "rev-fixture", "revisionNumericId": 1,
        "epoch": 0, "sceneName": "protocol-fixture",
        "source": {"kind": "synthetic", "physicsDt": 1 / 240, "basePublishHz": 30, "realtimeFactor": 1},
        "nodes": [
            {"entityKey": "/World", "kind": "group", "name": "World", "parentEntityKey": None},
            {"entityKey": "/World/Arm", "kind": "body", "streamId": 7, "name": "Arm", "parentEntityKey": "/World",
             "visuals": [{"kind": "primitive", "geometry": geometry, "localPose": identity} for geometry in geometries],
             "colliders": [{"kind": "primitive", "geometry": geometries[0], "localPose": identity}],
             "debug": {"newtonBodyIndex": 0}},
            {"entityKey": "/World/Floor", "kind": "static", "name": "Floor", "initialPose": identity,
             "visuals": [{"kind": "mesh", "meshId": "sha256-fixture", "url": "/meshes/sha256-fixture", "assetUpAxis": "Z", "scale": [1, 1, 1], "localPose": identity},
                         {"kind": "glb", "url": "/assets/floor.glb", "assetUpAxis": "Y", "scale": [1, 1, 1], "localPose": identity}]},
        ],
        "sites": [{"entityKey": "/World/Hinge/anchor", "role": "joint-anchor", "parentBodyKey": "/World/Arm", "jointKey": "/World/Hinge", "localPose": {"p": [0, 0, 0.3], "q": [0, 0, 0, 1]}},
                  {"entityKey": "/World/Reference", "role": "generic", "parentBodyKey": None, "localPose": identity}],
        "joints": [{"entityKey": "/World/Hinge", "type": "revolute", "parentBodyKey": None, "childBodyKey": "/World/Arm", "parentLocalPose": identity, "axis": [0, 0, 1], "anchorSiteKey": "/World/Hinge/anchor", "dofCount": 1}],
        "warnings": [{"code": "asset_fallback", "entityKey": "/World/Floor", "message": "Fixture illustrates fallback metadata"}],
    })


def main():
    schema = draft7(ProtocolContract.model_json_schema(mode="serialization"))
    schema["title"] = "ProtocolContract"
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["$id"] = "https://spatial-sync.local/protocol/v1/schema.json"
    write("types/protocol.schema.json", schema)
    manifest = manifest_sample()
    write("fixtures/manifest.json", manifest.model_dump(mode="json", exclude_none=True))
    frame_args = dict(sessionId=manifest.sessionId, sceneRevision=manifest.sceneRevision, epoch=0, seq=1, simTime=0, sampleTimeMs=1000, poses=[(7, 1, 2, 3, 0, 0, 0, 1)])
    state_args = dict(sessionId=manifest.sessionId, state="PAUSED", sceneRevision=manifest.sceneRevision, epoch=0, seq=1, simTime=0, realtimeFactor=1, actualRtf=0, cause={"kind": "command", "requestId": "cmd-fixture", "command": "pause"})
    messages = [
        Ready(sessionId=manifest.sessionId, sceneRevision=manifest.sceneRevision, revisionNumericId=1, epoch=0, sessionState="RUNNING", simTime=0, basePublishHz=30, realtimeFactor=1),
        Subscribe(sceneRevision=manifest.sceneRevision),
        Subscribe(sceneRevision=manifest.sceneRevision, scope="entities", entityKeys=["/World/Arm"]),
        SubscriptionAck(scope="all", grantedRateHz=30), Snapshot(**frame_args), TransformFrame(**frame_args),
        SessionState(**state_args), Heartbeat(state="PAUSED", simTime=0, lastSeq=1, actualRtf=0, serverTimeMs=1000),
        Command(requestId="cmd-fixture", command="pause"),
        CommandAck(requestId="cmd-fixture", status="applied", simTime=0, epoch=0),
        CommandAck(requestId="cmd-invalid", status="rejected", reason="invalid_state", simTime=0, epoch=0),
        EntityState(sessionId=manifest.sessionId, sceneRevision=manifest.sceneRevision, entityKey="/World", schema="device.example/0", stateSeq=1, simTime=0, payload={"running": True, "rpm": 3000, "unknown": {"nested": [None, "arbitrary"]}}),
        SceneReplaced(sessionId=manifest.sessionId, sceneRevision="rev-next", revisionNumericId=2),
        ServerError(code="revision_mismatch", message="Reload scene before subscribing"),
    ]
    write("fixtures/messages.json", [message.model_dump(mode="json", exclude_none=True) for message in messages])
    # Expected values are independently specified from the convention table, not computed by helpers under test.
    write("fixtures/math.json", {
        "primitiveCases": [
            {"geometry": {"type": "box", "halfExtents": [1, 2, 3]}, "expected": {"size": [2, 4, 6], "rotateX": 0}},
            {"geometry": {"type": "sphere", "radius": 2}, "expected": {"radius": 2, "rotateX": 0}},
            *[{"geometry": {"type": kind, "radius": 2, "halfHeight": 3}, "expected": {"radius": 2, "height": 6, "rotateX": math.pi / 2}} for kind in ("capsule", "cylinder", "cone")],
            {"geometry": {"type": "plane", "width": 0, "length": 4}, "expected": {"size": [100, 4], "rotateX": 0}},
        ],
        "poseCases": [{"name": "rotated-body-local-site", "body": {"p": [1, 2, 3], "q": [0, 0, 0.7071067811865476, 0.7071067811865476]}, "local": {"p": [1, 0, 2], "q": [0, 0, 0, 1]}, "expected": {"p": [1, 3, 5], "q": [0, 0, 0.7071067811865476, 0.7071067811865476]}}],
        "axisCases": [{"input": [1, 2, 3], "expected": [1, 3, -2]}],
        "hemisphereCases": [{"previous": [0, 0, 0, 1], "current": [0, 0, 0, -1], "expected": [0, 0, 0, 1]}],
    })


if __name__ == "__main__":
    main()
