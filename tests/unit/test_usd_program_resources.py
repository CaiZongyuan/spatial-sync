"""Existing program resource semantics, without importing an engine."""

import pytest

from packages.sim_runtime.library_scene import configured_weld_pairs


@pytest.mark.parametrize("mode", ["contact", "weld"])
def test_arm_opening_has_disabled_weld_pairs_for_either_grasp_mode(mode):
    bindings = [{"program": "robot_arm.newton", "config": {
        "handBody": "/hand", "tubes": ["/tube"], "graspMode": mode,
    }}]
    assert configured_weld_pairs(bindings, {"/hand": 8, "/tube": 11}, {}) == [(8, 11)]


def test_pairs_preserve_each_binding_and_deduplicate_slots_on_one_rotor():
    bindings = [
        {"program": "robot_arm.newton", "config": {"handBody": "/hand-a", "tubes": ["/tube-a"]}},
        {"program": "robot_arm.newton", "config": {"handBody": "/hand-b", "tubes": ["/tube-b"]}},
        {"program": "centrifuge.newton", "config": {"slotSites": ["/s1", "/s2", "/s3", "/s4"],
                                                    "tubes": ["/tube-a"]}},
        {"program": "tube.newton", "config": {"locationSites": ["/s1"]}},
    ]
    bodies = {"/hand-a": 8, "/hand-b": 9, "/tube-a": 11, "/tube-b": 12}
    sites = {key: 20 for key in ("/s1", "/s2", "/s3", "/s4")}
    assert configured_weld_pairs(bindings, bodies, sites) == [(8, 11), (9, 12), (20, 11)]


@pytest.mark.parametrize("site_owner,bodies", [(-1, {"/tube": 1}), (1, {"/tube": 1})])
def test_weld_rejects_world_static_or_self_endpoint(site_owner, bodies):
    binding = {"program": "centrifuge.newton", "config": {"slotSites": ["/slot"], "tubes": ["/tube"]}}
    with pytest.raises(ValueError, match="distinct imported bodies"):
        configured_weld_pairs([binding], bodies, {"/slot": site_owner})


def test_weld_rejects_missing_imported_reference():
    binding = {"program": "robot_arm.newton", "config": {"handBody": "/hand", "tubes": ["/missing"]}}
    with pytest.raises(ValueError, match="not imported"):
        configured_weld_pairs([binding], {"/hand": 8}, {})


def test_no_operating_programs_need_no_welds():
    assert configured_weld_pairs([], {}, {}) == []
