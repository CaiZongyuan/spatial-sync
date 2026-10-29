"""Client revision policy at the Operation Plan document boundary."""

from copy import deepcopy

import pytest

from tools.cell_passage_recipe import revise_remaining, revise_round_remaining


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


def test_unstarted_control_rebind_preserves_cycle_operation_identity():
    plan = {"id": "cp-round-stage3-part1-of2", "title": "Passage", "target": "session", "revision": 1,
            "steps": [
                {"id": 300101, "status": "pending", "do": {"entity": "/centrifuge", "action": "start",
                 "params": {"owner": "old", "epoch": 0, "operationId": "cycle-1", "rpm": 800,
                            "durationSeconds": 2}}},
                {"id": 300102, "status": "pending", "do": {"entity": "/workcell", "action": "pellet",
                 "params": {"owner": "old", "epoch": 0, "operationId": "apply-1", "cycle": "cycle-1",
                            "centrifuge": "/centrifuge", "vessels": ["S1", "S2", "S3", "S4"]}}},
            ]}
    result = revise_remaining(plan, owner="web-new", epoch=2, material_revision=8,
                              expected_material_revision=8)
    assert result["steps"][0]["do"]["params"]["operationId"] == "cycle-1"
    assert result["steps"][1]["do"]["params"]["cycle"] == "cycle-1"
    assert result["steps"][1]["do"]["params"]["owner"] == "web-new"


def test_failed_cycle_replacement_updates_pending_references_across_parts_and_keeps_done_parts():
    start = {"id": "start-part", "title": "Start", "revision": 1, "steps": [{
        "id": 300001, "status": "failed", "do": {"entity": "/cent", "action": "start", "params": {
            "owner": "old", "epoch": 0, "operationId": "failed-cycle", "rpm": 800, "durationSeconds": 2,
        }},
    }]}
    following = {"id": "following-part", "title": "Apply", "revision": 1, "steps": [
        {"id": 300002, "status": "pending", "check": {
            "entity": "/cent", "property": "completedCycle", "equals": "failed-cycle",
        }},
        {"id": 300003, "status": "pending", "do": {"entity": "/workcell", "action": "pellet", "params": {
            "owner": "old", "epoch": 0, "operationId": "apply-cycle", "cycle": "failed-cycle",
            "centrifuge": "/cent", "vessels": ["S1", "S2", "S3", "S4"],
        }}},
    ]}
    completed = {"id": "completed-part", "title": "Done", "revision": 1, "steps": [{
        "id": 100001, "status": "done", "check": {
            "entity": "/workcell", "property": "auditPassed", "equals": True,
        },
    }]}
    documents = revise_round_remaining(
        [completed, start, following], owner="web-new", epoch=2, material_revision=8,
        expected_material_revision=8, replacements={"start-part": {300001: deepcopy(start["steps"][0])}},
    )
    new_cycle = documents["start-part"]["steps"][0]["do"]["params"]["operationId"]
    assert new_cycle != "failed-cycle"
    assert documents["following-part"]["steps"][0]["check"]["equals"] == new_cycle
    assert documents["following-part"]["steps"][1]["do"]["params"]["cycle"] == new_cycle
    assert documents["following-part"]["steps"][1]["do"]["params"]["operationId"] == "apply-cycle"
    assert "completed-part" not in documents


def test_failed_fraction_requires_explicit_literal_remainder_instead_of_recomputing_current_fraction():
    step = {"id": 600001, "status": "failed", "do": {
        "entity": "/workcell", "action": "aspirateFraction", "params": {
            "owner": "old", "epoch": 0, "operationId": "partial-fraction", "vessel": "S1", "fraction": 0.5,
        },
    }}
    plan = {"id": "part", "title": "Partial allocation", "revision": 1, "steps": [step]}
    with pytest.raises(ValueError, match="literal remainder"):
        revise_remaining(plan, owner="new", epoch=2, material_revision=8, expected_material_revision=8,
                         replacements={600001: deepcopy(step)})
    replacement = {"id": 600001, "do": {"entity": "/workcell", "action": "aspirate", "params": {
        "vessel": "S1", "volumeUl": 40,
    }}}
    document = revise_remaining(
        plan, owner="new", epoch=2, material_revision=8, expected_material_revision=8,
        replacements={600001: replacement},
    )
    assert document["steps"][0]["do"]["params"]["volumeUl"] == 40
