import math

import pytest

from packages.programs.labware.facts import LabwareFactsProgram, seated_station


def test_station_requires_real_pose_orientation_and_relative_rest():
    stations = [[1, 2, 0.8, 0, 0, 0, 1]]
    pose = [1, 2, 0.8, 0, 0, 0, 1]
    assert seated_station(pose, [[0, 0, 0]], stations, ["incubator"]) == "incubator"
    assert seated_station([1.006, 2, 0.8, 0, 0, 0, 1], [[0, 0, 0]], stations, ["incubator"]) is None
    assert seated_station(pose, [[0.02, 0, 0]], stations, ["incubator"]) is None
    tilted = [1, 2, 0.8, 0, math.sin(math.radians(6) / 2), 0, math.cos(math.radians(6) / 2)]
    assert seated_station(tilted, [[0, 0, 0]], stations, ["incubator"]) is None
    assert seated_station(pose, [[0, 0, 0]], stations, ["incubator"], held=True) is None


def test_rotated_station_accepts_matching_orientation_but_not_wrong_yaw():
    s = math.sqrt(0.5)
    station = [[1, 2, 0.8, 0, 0, s, s]]
    assert seated_station([1, 2, 0.8, 0, 0, s, s], [[0, 0, 0]], station, ["imager"]) == "imager"
    assert seated_station([1, 2, 0.8, 0, 0, 0, 1], [[0, 0, 0]], station, ["imager"]) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_unknown_mechanics_never_establish_seating(bad):
    assert seated_station([bad, 0, 0, 0, 0, 0, 1], [[0, 0, 0]], [[0, 0, 0, 0, 0, 0, 1]], ["a"]) is None


def test_lidless_config_omits_null_spatial_reference_but_preserves_real_lid_identity():
    bare = LabwareFactsProgram.Config(robots=[], locationSites=[]).model_dump(mode="json")
    assert "lidBody" not in bare
    covered = LabwareFactsProgram.Config(robots=[], locationSites=[], lidBody="/World/Lab/Lid")
    assert covered.model_dump(mode="json")["lidBody"] == "/World/Lab/Lid"
