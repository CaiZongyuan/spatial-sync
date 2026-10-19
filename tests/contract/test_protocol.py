import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from packages.protocol.schema import (
    ActionRequest,
    ClientMessage,
    Command,
    CommandAck,
    OperationPlan,
    PlanUpdate,
    Pose,
    SceneManifest,
    ServerMessage,
    Snapshot,
    StepArgs,
    Subscribe,
    TransformFrame,
    validate_command_args,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "packages/protocol/fixtures"


@pytest.fixture
def manifest():
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


def test_python_sample_roundtrips(manifest):
    parsed = SceneManifest.model_validate(manifest)
    assert parsed.model_dump(mode="json", exclude_none=True) == manifest
    for message in json.loads((FIXTURES / "messages.json").read_text(encoding="utf-8")):
        adapter = TypeAdapter(
            ClientMessage
            if message["type"] in ("client.subscribe", "command.request")
            or message["type"].startswith("action.")
            else ServerMessage
        )
        result = adapter.validate_json(json.dumps(message))
        assert result.model_dump(mode="json", exclude_none=True) == message


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m["nodes"][1].update(streamId=-1),
        lambda m: m["nodes"][1].update(streamId=2**32),
        lambda m: m["nodes"][1].update(streamId=True),
        lambda m: m["nodes"][1].update(streamId="7"),
        lambda m: m["nodes"][0].update(streamId=1),
        lambda m: m["nodes"][1].update(entityKey="World/Arm"),
        lambda m: m["nodes"][1].update(entityKey="/World//Arm"),
        lambda m: m["nodes"][1].update(parentEntityKey="/Missing"),
        lambda m: m["nodes"][0].update(parentEntityKey="/World/Arm"),
        lambda m: m["nodes"].append(deepcopy(m["nodes"][1])),
        lambda m: m["sites"][0].update(entityKey=m["nodes"][0]["entityKey"]),
        lambda m: m["sites"][0].update(parentBodyKey="/World/Floor"),
        lambda m: m["sites"][0].update(jointKey="/Missing"),
        lambda m: m["joints"][0].update(anchorSiteKey="/Missing"),
        lambda m: m["joints"][0].update(axis=[0, 0, 0]),
        lambda m: m["joints"][0].update(dofCount=2),
        lambda m: m["nodes"][1]["visuals"][0]["geometry"].update(halfExtents=[-1, 1, 1]),
        lambda m: m["nodes"][1]["visuals"][0]["localPose"].update(q=[0, 0, 0, 2]),
        lambda m: m["source"].update(realtimeFactor=float("nan")),
        lambda m: m["coordinates"].update(upAxis="Y"),
        lambda m: m.update(protocolVersion=True),
        lambda m: m["nodes"][1].update(stableKey="true"),
        lambda m: m["nodes"][1].update(
            fallbackVisuals=[
                {
                    "kind": "primitive",
                    "geometry": {"type": "sphere", "radius": 0.1},
                    "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 2]},
                }
            ]
        ),
    ],
)
def test_manifest_rejects_invalid_boundary(manifest, mutation):
    mutation(manifest)
    with pytest.raises(ValidationError):
        SceneManifest.model_validate(manifest)


def test_duplicate_streams_differing_keys(manifest):
    second = deepcopy(manifest["nodes"][1])
    second["entityKey"] = "/World/AnotherArm"
    manifest["nodes"].append(second)
    with pytest.raises(ValidationError, match="streamId must be unique"):
        SceneManifest.model_validate(manifest)


def test_frame_snapshot_and_rejections():
    fields = dict(
        sessionId="s",
        sceneRevision="r",
        epoch=0,
        seq=0,
        simTime=0,
        sampleTimeMs=0,
        poses=[(2, 1, 2, 3, 0, 0, 0, 1)],
    )
    assert Snapshot(**fields).type == "stream.snapshot"
    assert TransformFrame(type="stream.snapshot", **fields).type == "stream.snapshot"
    for rows in [
        [(2, 1, 2, 3, 0, 0, 0, 1)] * 2,
        [(2, float("inf"), 2, 3, 0, 0, 0, 1)],
        [(2, 1, 2, 3, 0, 0, 0, 2)],
    ]:
        with pytest.raises(ValidationError):
            TransformFrame(**(fields | {"poses": rows}))
    with pytest.raises(ValidationError):
        TransformFrame(**fields, paused=True)


def test_frame_wire_precision():
    frame = TransformFrame(
        sessionId="s",
        sceneRevision="r",
        epoch=0,
        seq=1,
        simTime=0,
        sampleTimeMs=0,
        poses=[(2, 1.12345678, 0, 0, 0, 0, 0.7071067811865476, 0.7071067811865476)],
    )
    assert frame.model_dump(mode="json")["poses"][0] == [2, 1.123457, 0, 0, 0, 0, 0.7071068, 0.7071068]


@pytest.mark.parametrize("count", [0, -1, 1.5, True, "2", None, 1001])
def test_step_count_is_positive_integer(count):
    with pytest.raises(ValidationError):
        validate_command_args(Command(requestId="x", command="step", args={"count": count}))


def test_command_validation_supports_rejection_semantics():
    assert validate_command_args(Command(requestId="x", command="step")) == StepArgs(count=1)
    assert validate_command_args(Command(requestId="x", command="step", args={"count": 1000})).count == 1000
    with pytest.raises(ValueError, match="unknown_command"):
        validate_command_args(Command(requestId="x", command="future-command"))
    with pytest.raises(ValidationError):
        validate_command_args(Command(requestId="x", command="pause", args={"oops": 1}))
    for status, reason in [("applied", "invalid_args"), ("rejected", None)]:
        with pytest.raises(ValidationError):
            CommandAck(requestId="x", status=status, reason=reason, simTime=0, epoch=0)


def test_subscription_v2_is_full_binary_and_info_defaults_enabled():
    request = Subscribe(sceneRevision="r")
    assert request.protocolVersion == 2 and request.info is True
    assert Subscribe(sceneRevision="r", info=False).info is False
    for field, value in [
        ("scope", "all"),
        ("delta", False),
        ("format", "binary"),
        ("entityKeys", ["/World"]),
        ("deviceKey", "/World"),
        ("viewport", {}),
        ("protocolVersion", 1),
        ("info", "true"),
    ]:
        with pytest.raises(ValidationError):
            Subscribe.model_validate({"sceneRevision": "r", field: value})


def test_reserved_plan_correlation_is_not_silently_accepted_before_runtime_support():
    with pytest.raises(ValidationError):
        ActionRequest(requestId="future", entityKey="/World", action="start", planId="plan-1", stepId=1)


@pytest.mark.parametrize("step", [
    {"id": 1},
    {"id": 1, "do": {"entity": "/World", "action": "start"},
     "check": {"property": "phase", "equals": "idle"}},
    {"id": 1, "wait": {"property": "phase", "equals": "idle"},
     "expect": {"property": "phase", "equals": "idle"}},
    {"id": 0, "check": {"property": "phase", "equals": "idle"}},
])
def test_operation_plan_step_forms_are_exclusive_and_expect_belongs_to_do(step):
    with pytest.raises(ValidationError):
        OperationPlan(id="plan-1", title="Operation", target="session", revision=1, steps=[step])


def test_plan_step_delta_requires_a_positive_revision_and_step_id():
    payload = {"type": "plan.update", "update": "step", "sceneRevision": "r", "epoch": 0,
               "simTime": 1, "sampleTimeMs": 1000, "planId": "plan-1", "revision": 1,
               "stepId": 1, "status": "done"}
    assert TypeAdapter(PlanUpdate).validate_python(payload).status == "done"
    for field in ("revision", "stepId"):
        with pytest.raises(ValidationError):
            TypeAdapter(PlanUpdate).validate_python(payload | {field: 0})


def test_quaternion_not_silently_normalized_at_boundary():
    with pytest.raises(ValidationError):
        Pose(p=[0, 0, 0], q=[0, 0, 0, 2])


def test_generated_json_artifacts_are_current():
    subprocess.run(
        [sys.executable, str(ROOT / "packages/protocol/scripts/export_schema.py"), "--check"],
        cwd=ROOT,
        check=True,
    )
