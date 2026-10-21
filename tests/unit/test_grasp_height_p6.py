"""Exercise the P6 consumer with model-shaped interface fixtures, without SDKs."""

import sys
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from packages.library.physics import taught_checks

TCP, HAND, BOTTOM, JOINT = ("/World/Lab/Robot/hand/tcp", "/World/Lab/Robot/hand",
                            "/World/Lab/Bottom", "/World/Lab/Robot/joint")


class Array:
    def __init__(self, value, dtype=None):
        self.value = np.array(value, dtype=dtype)

    def numpy(self):
        return self.value

    def assign(self, value):
        self.value = value


@pytest.fixture
def model_fixture(monkeypatch):
    monkeypatch.setitem(sys.modules, "newton", SimpleNamespace(
        ShapeFlags=SimpleNamespace(SITE=8), eval_fk=lambda *args: None))
    config = {"tcpSite": TCP, "graspHeightM": .080, "jointKeys": [JOINT],
              "locations": {"locations": {name: {"site": BOTTOM, "grasp": [0.]}
                                           for name in ("rack.A1", "centrifuge.slot1")}}}
    calibration = {"assetId": "robot/fr3@1", "assetContentHash": "a" * 64,
                   "geometryDigest": "b" * 64, "handBody": HAND, "tcpSite": TCP,
                   "siteOwner": HAND, "handToTcpPose": [0, 0, .1034, 0, 0, 0, 1]}
    model = SimpleNamespace(
        shape_flags=Array([8, 8]), shape_label=[TCP, BOTTOM], shape_body=Array([0, -1]),
        shape_transform=Array([[0, 0, .1034, 0, 0, 0, 1], [0, 0, 1, 0, 0, 0, 1]], np.float32),
        body_label=[HAND], joint_label=[JOINT], joint_q_start=Array([0, 1]),
        joint_qd_start=Array([0, 1]), joint_articulation=Array([0]), articulation_count=1,
        joint_limit_lower=Array([-1]), joint_limit_upper=Array([1]),
        state=lambda: SimpleNamespace(joint_q=Array([0.]), joint_qd=Array([0.]),
                                      body_q=Array([[0, 0, .8966, 0, 0, 0, 1]])),
    )
    receipt = {"expected": calibration, "composed": deepcopy(calibration)}
    sim = SimpleNamespace(model=model, builder=SimpleNamespace(spatial_grasp_calibrations={"R": receipt}))
    program = {"fields": {"tcpSite": {"spatialReference": "site", "postureEndpoint": {
                    "offsetField": "graspHeightM", "offsetAxis": "worldZ"}},
                "jointKeys": {"posture": True}, "locations": {"documentSchema": "locations"},
                "ordinarySite": {"spatialReference": "site"}},
               "configSchema": {"properties": {"graspHeightM": {"type": "number", "minimum": 0}}}}
    payload = {"programs": {"fixture": program}, "structure": {"effectiveBindings": {"newton": [
        {"key": "R", "program": "fixture", "config": config}]}}}
    return sim, payload


def test_real_consumer_uses_explicit_endpoint_and_reports_all_old_bottom_vectors_80mm(model_fixture):
    rows = taught_checks(*model_fixture)
    assert {row["location"] for row in rows} == {"rack.A1", "centrifuge.slot1"}
    assert all(row["status"] == "fail" for row in rows)
    for row in rows:
        assert row["measurements"]["errorMm"] == pytest.approx(80, abs=.001)
        assert row["measurements"]["bottomSiteWorld"] == [0, 0, 1]
        assert row["measurements"]["graspTargetWorld"] == [0, 0, 1.08]


@pytest.mark.parametrize("change", ["receipt", "owner", "local-pose", "role"])
def test_p6_fails_each_location_before_point_comparison_for_untrusted_physical_frame(model_fixture, change):
    sim, payload = model_fixture
    if change == "receipt":
        sim.builder.spatial_grasp_calibrations.clear()
    elif change == "owner":
        sim.model.body_label[0] = "/World/Lab/FakeHand"
    elif change == "local-pose":
        sim.model.shape_transform.value[0, 2] += .000001  # Even one micron is not a storage rounding.
    else:
        payload["programs"]["fixture"]["fields"]["tcpSite"].pop("postureEndpoint")
    rows = taught_checks(sim, payload)
    assert len(rows) == 2
    assert all(row["status"] == "fail" and "measurements" not in row for row in rows)
