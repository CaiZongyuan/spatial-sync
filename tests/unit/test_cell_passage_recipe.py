"""Client revision policy at the Operation Plan document boundary."""

from copy import deepcopy

import pytest

from tools.cell_passage_recipe import revise_remaining


def test_revision_preserves_done_and_requires_reviewed_replacement_for_partial_failure():
    plan = {"id": "p1", "title": "Passage", "target": "session", "revision": 1, "steps": [
        {"id": 200001, "status": "done", "do": {"entity": "/workcell", "action": "aspirate",
         "params": {"owner": "old", "epoch": 0, "operationId": "done", "volumeUl": 100}}},
        {"id": 200002, "status": "failed", "do": {"entity": "/workcell", "action": "dispense",
         "params": {"owner": "old", "epoch": 0, "operationId": "partial", "volumeUl": 100}}},
    ]}
    with pytest.raises(ValueError, match="replacement"):
        revise_remaining(plan, owner="web-new", epoch=2, material_revision=8,
                         expected_material_revision=8)
    replacement = deepcopy(plan["steps"][1])
    replacement["do"]["params"]["volumeUl"] = 40
    result = revise_remaining(plan, owner="web-new", epoch=2, material_revision=8,
                              expected_material_revision=8, replacements={200002: replacement})
    assert result["steps"][0]["do"] == plan["steps"][0]["do"]
    assert result["steps"][1]["do"]["params"] == {
        "owner": "web-new", "epoch": 2, "operationId": "passage-p1-r2-s200002-e2", "volumeUl": 40,
    }
    with pytest.raises(ValueError, match="material"):
        revise_remaining(plan, owner="web-new", epoch=2, material_revision=9,
                         expected_material_revision=8, replacements={200002: replacement})
