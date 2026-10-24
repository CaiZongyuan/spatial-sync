"""Training framing carries env identity without mutating strict spatial/recording payloads."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from packages.protocol.schema import TransformFrame
from packages.training.view_contract import VIEW_MESSAGE


def envelope():
    return {
        "schema": "lw.training-view/1",
        "type": "frame",
        "jobId": "job-fixture",
        "attemptId": "attempt-fixture",
        "viewId": "view-fixture",
        "envIndex": 1,
        "payload": {
            "protocolVersion": 2,
            "type": "stream.snapshot",
            "sessionId": "training-view:view-fixture:env:1",
            "sceneRevision": "training-scene-fixture",
            "epoch": 2,
            "seq": 8,
            "simTime": 3.0,
            "sampleTimeMs": 1000.0,
            "poses": [[7, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0]],
        },
    }


def test_training_wrapper_keeps_spatial_v2_unchanged_and_does_not_add_env_fields():
    raw = envelope()
    parsed = VIEW_MESSAGE.validate_python(raw)
    assert parsed.envIndex == 1 and parsed.payload == TransformFrame.model_validate(raw["payload"])
    assert parsed.model_dump(mode="json")["schema"] == "lw.training-view/1"
    invalid = raw["payload"] | {"envIndex": 1}
    with pytest.raises(ValidationError):
        TransformFrame.model_validate(invalid)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "lw.training-view/2"),
        ("type", "action.request"),
        ("envIndex", 4),
        ("envIndex", True),
        ("viewId", None),
    ],
)
def test_unknown_transport_or_invalid_environment_never_reaches_spatial_buffer(field, value):
    raw = envelope()
    raw[field] = value
    with pytest.raises(ValidationError):
        VIEW_MESSAGE.validate_python(raw)


@pytest.mark.parametrize("change", ["identity", "nonfinite", "quaternion", "duplicate"])
def test_bad_pose_identity_or_complete_table_is_rejected(change):
    raw = deepcopy(envelope())
    if change == "identity":
        raw["payload"]["sessionId"] = "session-ordinary"
    elif change == "nonfinite":
        raw["payload"]["poses"][0][1] = float("nan")
    elif change == "quaternion":
        raw["payload"]["poses"][0][7] = 2.0
    else:
        raw["payload"]["poses"].append(raw["payload"]["poses"][0][:])
    with pytest.raises(ValidationError):
        VIEW_MESSAGE.validate_python(raw)
