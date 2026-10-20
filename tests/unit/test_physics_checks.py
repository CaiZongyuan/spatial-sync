"""Physics thresholds use independent SI examples without importing an engine."""

import builtins
import importlib.util
import json
import math
from pathlib import Path

import pytest

from packages.library import physics_checks as rules


def test_decision_module_import_does_not_import_newton_or_warp(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in ("newton", "warp"):
            raise AssertionError("Pure decisions imported a physics SDK")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    spec = importlib.util.spec_from_file_location("pure_physics_decisions", Path(rules.__file__))
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_loaded_finite_state_and_actual_nonfinite_failure_are_distinct():
    assert rules.finite_state({"body_q": [0.0, 1.0], "joint_q": []})["status"] == "pass"
    assert rules.finite_state({})["status"] == "fail"
    failed = rules.finite_state({"body_q": [math.nan], "joint_q": [0.0]})
    assert failed["status"] == "fail" and failed["measurements"]["invalidArrays"] == ["body_q"]
    json.dumps(failed, allow_nan=False)


@pytest.mark.parametrize("position,status", [([0.003, 0.004, 0], "pass"),
                                             ([0, 0, math.nextafter(0.005, math.inf)], "fail")])
def test_p2_uses_euclidean_five_mm_inclusive_without_axis_or_warmup_allowance(position, status):
    assert rules.settling("/World/Free", [0, 0, 0], position)["status"] == status


def test_p3_uses_signed_separation_and_the_worst_contact():
    assert rules.penetration([])["status"] == "pass"
    assert rules.penetration([("/A", "/Ground", -0.002)])["status"] == "pass"
    result = rules.penetration([("/A", "/Ground", 0.1), ("/B", "/A", -0.003)])
    assert result["status"] == "fail" and result["entityKey"] == "/B"
    assert result["measurements"]["depthM"] == 0.003


def test_p3_never_claims_complete_pass_for_unqueried_world_static_pairs():
    empty = rules.penetration([], world_static_shapes=2)
    assert empty["status"] == "warn" and empty["measurements"]["excludedWorldStaticPairCount"] == 1
    measured = rules.penetration([("/A", "/B", -0.001)], world_static_shapes=3)
    assert measured["status"] == "warn" and measured["measurements"]["depthM"] == 0.001
    assert measured["measurements"]["excludedWorldStaticPairCount"] == 3
    failed = rules.penetration([("/A", "/B", -0.003)], world_static_shapes=2)
    assert failed["status"] == "fail" and failed["measurements"]["depthM"] == 0.003


@pytest.mark.parametrize("mass,status", [(0.05, "pass"), (20.0, "pass"),
                                         (0.049, "fail"), (20.001, "fail")])
def test_density_is_kg_per_collision_cubic_metre_with_inclusive_bounds(mass, status):
    assert rules.mass_density("/Asset", mass, 0.001)["status"] == status


@pytest.mark.parametrize("mass,volume", [(0, 1), (-1, 1), (1, 0), (1, -1),
                                         (math.nan, 1), (1, math.inf)])
def test_invalid_physical_mass_or_volume_cannot_be_a_pass(mass, volume):
    result = rules.mass_density("/Asset", mass, volume, estimated=True)
    assert result["status"] == "fail"
    json.dumps(result, allow_nan=False)


def test_estimated_mass_keeps_its_warning_after_a_valid_density():
    assert rules.mass_density("/Asset", 1.0, 0.001, estimated=True)["status"] == "warn"


def test_p5_respects_units_bounds_and_unbounded_joints():
    assert rules.joint_limit("/Joint", 0.2, -0.2, 0.2, unit="rad")["status"] == "pass"
    assert rules.joint_limit("/Joint", 0.02, 0, 0.01, unit="m")["status"] == "fail"
    assert rules.joint_limit("/Continuous", 40.0, None, None, unit="rad")["notApplicable"]


def test_p6_compares_actual_world_tcp_and_site_in_millimetres():
    good = rules.tcp_error("/World/Arm", "station", [1.003, 0.004, 0], [1.0, 0, 0])
    # Use an exact decimal-axis boundary independently of a translated vector's rounding.
    boundary = rules.tcp_error("/World/Arm", "station", [0.01, 0, 0], [0, 0, 0])
    bad = rules.tcp_error("/World/Arm", "station", [0, 0, 0.038], [0, 0, 0])
    assert good["status"] == "pass" and good["measurements"]["errorMm"] == pytest.approx(5)
    assert boundary["status"] == "pass"
    assert bad["status"] == "fail" and bad["location"] == "station"
    assert bad["measurements"]["errorMm"] == 38
    assert "38.000 mm" in bad["message"]


@pytest.mark.parametrize("decision", [
    lambda: rules.settling("/Body", [0, 0, 0], [0, math.nan, 0]),
    lambda: rules.penetration([("/Body", "/Ground", math.inf)]),
    lambda: rules.tcp_error("/Arm", "station", [0, 0, 0], [math.nan, 0, 0]),
    lambda: rules.settling("/Body", [-1e308, 0, 0], [1e308, 0, 0]),
    lambda: rules.tcp_error("/Arm", "station", [-1e308, 0, 0], [1e308, 0, 0]),
])
def test_nonfinite_measurements_fail_without_serializing_invalid_json(decision):
    result = decision()
    assert result["status"] == "fail"
    json.dumps(result, allow_nan=False)
