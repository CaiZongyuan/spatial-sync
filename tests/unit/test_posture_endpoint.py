"""Metadata and calibration counterexamples without USD or Newton."""

import copy

import pytest

from packages.library.posture import posture_check, resolve_endpoint

FIELDS = {
    "tcpSite": {
        "spatialReference": "site",
        "postureEndpoint": {"offsetField": "graspHeightM", "offsetAxis": "worldZ"},
    },
    "otherSite": {"spatialReference": "site"},
}
SCHEMA = {"properties": {"graspHeightM": {"type": "number", "minimum": 0, "default": 0.0}}}
CONFIG = {"tcpSite": "/World/Lab/R/hand/tcp", "otherSite": "/World/Lab/Other", "graspHeightM": 0.080}
CALIBRATION = {
    "assetId": "robot/fr3@1",
    "assetContentHash": "a" * 64,
    "handBody": "/World/Lab/R/hand",
    "tcpSite": "/World/Lab/R/hand/tcp",
    "siteOwner": "/World/Lab/R/hand",
    "handToTcpPose": [0, 0, 0.1034, 0, 0, 0, 1],
    "geometryDigest": "b" * 64,
}


def test_explicit_endpoint_ignores_an_extra_ordinary_site():
    endpoint = resolve_endpoint(FIELDS, CONFIG, SCHEMA)
    assert (endpoint.field, endpoint.height, endpoint.explicit) == ("tcpSite", 0.080, True)


def test_unique_legacy_site_is_only_a_zero_offset_fallback():
    fields = {"tcpSite": {"spatialReference": "site"}}
    assert resolve_endpoint(fields, {"tcpSite": "tcp"}, {}).height == 0
    with pytest.raises(ValueError, match="nonzero"):
        resolve_endpoint(fields, CONFIG, SCHEMA)


def test_multiple_unmarked_sites_do_not_gain_a_legacy_endpoint():
    fields = {"tcpSite": {"spatialReference": "site"}, "otherSite": {"spatialReference": "site"}}
    with pytest.raises(ValueError, match="exactly one"):
        resolve_endpoint(fields, CONFIG | {"graspHeightM": 0}, SCHEMA)


@pytest.mark.parametrize("height", [True, "0.08", float("nan")])
def test_direct_endpoint_resolution_does_not_coerce_unchecked_offset_values(height):
    with pytest.raises(ValueError, match="finite nonnegative"):
        resolve_endpoint(FIELDS, CONFIG | {"graspHeightM": height}, SCHEMA)


@pytest.mark.parametrize("change", ["duplicate", "unknown-field", "wrong-axis", "not-number"])
def test_bad_registered_metadata_cannot_become_an_implicit_endpoint(change):
    fields, schema = copy.deepcopy(FIELDS), copy.deepcopy(SCHEMA)
    if change == "duplicate":
        fields["otherSite"]["postureEndpoint"] = copy.deepcopy(fields["tcpSite"]["postureEndpoint"])
    elif change == "unknown-field":
        fields["tcpSite"]["postureEndpoint"]["offsetField"] = "madeUp"
    elif change == "wrong-axis":
        fields["tcpSite"]["postureEndpoint"]["offsetAxis"] = "tcpZ"
    else:
        schema["properties"]["graspHeightM"]["type"] = "string"
    with pytest.raises(ValueError):
        resolve_endpoint(fields, CONFIG, schema)


def test_old_bottom_fk_with_eighty_mm_height_reports_eighty_mm_error():
    endpoint = resolve_endpoint(FIELDS, CONFIG, SCHEMA)
    row = posture_check(
        "R",
        "rack.A1",
        [1, 2, 3],
        [1, 2, 3],
        endpoint,
        expected_calibration=CALIBRATION,
        observed_calibration=CALIBRATION,
    )
    assert row["status"] == "fail"
    assert row["measurements"]["errorM"] == pytest.approx(0.080)
    assert row["measurements"]["bottomSiteWorld"] == [1, 2, 3]
    assert row["measurements"]["graspTargetWorld"] == pytest.approx([1, 2, 3.080])
    assert row["measurements"]["physicalTcpWorld"] == [1, 2, 3]


@pytest.mark.parametrize(
    "change", ["unknown", "virtual-site", "wrong-owner", "moved-frame", "changed-fingers"]
)
def test_zero_distance_does_not_override_unknown_or_wrong_physical_calibration(change):
    endpoint = resolve_endpoint(FIELDS, CONFIG, SCHEMA)
    observed = copy.deepcopy(CALIBRATION)
    if change == "unknown":
        observed = None
    elif change == "virtual-site":
        observed["tcpSite"] = "/World/Lab/R/virtualBottomTcp"
    elif change == "wrong-owner":
        observed["siteOwner"] = "/World/Lab/Tubes/T1"
    elif change == "moved-frame":
        observed["handToTcpPose"][2] += 0.080
    else:
        observed["geometryDigest"] = "c" * 64
    row = posture_check(
        "R",
        "rack.A1",
        [1, 2, 3.080],
        [1, 2, 3],
        endpoint,
        expected_calibration=CALIBRATION,
        observed_calibration=observed,
    )
    assert row["status"] == "fail"
    assert "calibration" in row["message"].lower()
    assert "measurements" not in row  # An untrusted frame has no invented error observation.


def test_old_zero_measurements_keep_their_original_values_and_do_not_need_new_calibration():
    endpoint = resolve_endpoint({"tcpSite": {"spatialReference": "site"}}, {"tcpSite": "tcp"}, {})
    row = posture_check("R", "rack.A1", [1, 2, 3.009], [1, 2, 3], endpoint)
    assert row["status"] == "pass"
    assert row["measurements"]["errorM"] == pytest.approx(0.009)
    assert row["measurements"]["siteWorldM"] == [1, 2, 3]
