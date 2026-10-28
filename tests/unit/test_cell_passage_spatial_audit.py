"""Worked transform samples, not a Newton execution or transport qualification."""

from copy import deepcopy

from tools.cell_passage_audit import audit_spatial_state


def samples():
    manifest = {"sessionId": "s", "sceneRevision": "r", "source": {"kind": "synthetic"}, "nodes": [
        {"kind": "body", "entityKey": "/tube", "streamId": 1},
        {"kind": "body", "entityKey": "/lid", "streamId": 2},
    ], "sites": [
        {"entityKey": "/rack", "parentBodyKey": None, "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}},
        {"entityKey": "/tube/LidSeat", "parentBodyKey": "/tube",
         "localPose": {"p": [0, 0, 0.05], "q": [0, 0, 0, 1]}},
    ]}
    first = {"sessionId": "s", "sceneRevision": "r", "epoch": 0, "simTime": 0,
             "poses": [[1, 0, 0, 0, 0, 0, 0, 1], [2, 0, 0, 0.05, 0, 0, 0, 1]]}
    second = {**deepcopy(first), "simTime": 0.1}
    expected = {"/tube": "/rack", "/lid": "/tube/LidSeat"}
    return manifest, first, second, expected


def test_spatial_audit_checks_actual_body_and_moving_lid_site_in_one_coordinate_system():
    manifest, first, second, expected = samples()
    result = audit_spatial_state(manifest, first, second, expected)
    assert result["status"] == "spatial_state_valid"
    assert result["nativeSource"] is False and result["experimentQualified"] is False
    displaced = deepcopy(second)
    displaced["poses"][0][1] = 0.01
    displaced["poses"][1][1] = 0.01
    assert audit_spatial_state(manifest, first, displaced, expected)["status"] == "invalid"


def test_nearby_but_moving_or_wrong_lid_orientation_does_not_count_as_seated():
    manifest, first, second, expected = samples()
    second["poses"][0][1] = 0.002
    second["poses"][1][1] = 0.002
    assert audit_spatial_state(manifest, first, second, expected)["status"] == "invalid"
    manifest, first, second, expected = samples()
    second["poses"][1][4:] = [0, 0, 1, 0]
    assert audit_spatial_state(manifest, first, second, expected)["status"] == "invalid"
