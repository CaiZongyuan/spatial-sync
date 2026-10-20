"""The actual toy Manifest is the height authority; no Source starts here."""

from copy import deepcopy

import pytest

from apps.api.app.library_validation import program_schemas
from packages.library.grasp_calibration import grasp_checks
from packages.protocol.internal import SessionOptions
from packages.sim_runtime.synthetic_geometry import tube_height
from packages.sim_runtime.synthetic_lab import BASE, ROBOT, build_manifest

TUBE = BASE + "/Tubes/Tube01"


def test_actual_synthetic_tubes_are_bottom_origin_eighty_mm_cylinders():
    manifest = build_manifest("height-test", SessionOptions(device="cpu"))
    assert tube_height(manifest, TUBE) == .080
    node = next(node for node in manifest.nodes if node.entityKey == TUBE)
    node.visuals[0].localPose.p = [0, 0, .041]
    with pytest.raises(ValueError, match="bottom-origin"):
        tube_height(manifest, TUBE)


@pytest.mark.parametrize("height,known,okay", [(.080, True, True), (.081, True, False), (.020, False, False)])
def test_s5_virtual_uses_actual_geometry_and_rejects_unknown_height(height, known, okay):
    manifest = build_manifest("height-test", SessionOptions(device="cpu"))
    binding = {"key": ROBOT, "program": "robot_arm.virtual", "config": {"graspHeightM": height,
                                                                                    "tubes": [TUBE]}}
    resources = {"tubeHeights": {TUBE: tube_height(manifest, TUBE)} if known else {}}
    problems, _ = grasp_checks(None, [binding], program_schemas(), {}, None, resources)
    assert bool(problems) is not okay


def test_s5_virtual_shared_tube_conflicts_include_default_zero_arm():
    binding = {"key": ROBOT, "program": "robot_arm.virtual", "config": {"tubes": [TUBE]}}
    other = deepcopy(binding)
    other["key"] += "B"
    other["config"]["graspHeightM"] = .020
    problems, _ = grasp_checks(None, [binding, other], program_schemas(), {}, None,
                               {"tubeHeights": {TUBE: .080}})
    assert "Conflicting" in " ".join(problems)
