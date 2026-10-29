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


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_cycle_replacement_updates_pending_references_across_parts_and_keeps_done_parts(status):
    start = {"id": "start-part", "title": "Start", "revision": 1, "steps": [{
        "id": 300001, "status": status, "do": {"entity": "/cent", "action": "start", "params": {
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


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_interrupted_step_requires_review_and_can_be_replaced_with_a_check(status):
    original = {"id": 100001, "status": status, "do": {
        "entity": "/workcell", "action": "wait", "params": {
            "owner": "old", "epoch": 0, "operationId": "skipped-wait", "seconds": 2,
        },
    }}
    plan = {"id": "skipped-part", "title": "Skipped", "revision": 1, "steps": [original]}
    checkpoint = dict(owner="new", epoch=2, material_revision=8, expected_material_revision=8)
    with pytest.raises(ValueError, match="replacement"):
        revise_round_remaining([plan], **checkpoint)
    replacement = {"id": 100001, "check": {
        "entity": "/workcell", "property": "controlSafe", "equals": True,
    }}
    before = deepcopy(plan)
    documents = revise_round_remaining(
        [plan], **checkpoint, replacements={plan["id"]: {100001: replacement}},
    )
    assert documents[plan["id"]]["steps"] == [replacement]
    assert plan == before


def test_skipped_cycle_replacement_updates_references_in_the_same_part():
    plan = {"id": "cycle-part", "title": "Skipped cycle", "revision": 1, "steps": [
        {"id": 300001, "status": "skipped", "do": {
            "entity": "/cent", "action": "start", "params": {
                "owner": "old", "epoch": 0, "operationId": "skipped-cycle", "rpm": 800,
                "durationSeconds": 2,
            },
        }},
        {"id": 300002, "status": "pending", "check": {
            "entity": "/cent", "property": "completedCycle", "equals": "skipped-cycle",
        }},
    ]}
    documents = revise_round_remaining(
        [plan], owner="new", epoch=2, material_revision=8, expected_material_revision=8,
        replacements={plan["id"]: {300001: deepcopy(plan["steps"][0])}},
    )
    steps = documents[plan["id"]]["steps"]
    assert steps[0]["do"]["params"]["operationId"] == "passage-cycle-part-r2-s300001-e2"
    assert steps[1]["check"]["equals"] == steps[0]["do"]["params"]["operationId"]


@pytest.mark.parametrize("status", ["done", "running"])
def test_replacements_cannot_rewrite_done_or_replay_in_flight_actions(status):
    step = {"id": 1, "status": status, "do": {"entity": "/workcell", "action": "wait", "params": {
        "owner": "old", "epoch": 0, "operationId": "existing", "seconds": 1,
    }}}
    plan = {"id": "part", "title": "Protected", "revision": 1, "steps": [step]}
    with pytest.raises(ValueError, match="preserved|Observe"):
        revise_remaining(plan, owner="new", epoch=2, material_revision=8, expected_material_revision=8,
                         replacements={1: deepcopy(step)})


@pytest.mark.parametrize("explicit_replacement", [False, True])
def test_admitted_pending_step_remains_observe_only_even_with_a_replacement(explicit_replacement):
    step = {"id": 1, "status": "pending", "do": {"entity": "/workcell", "action": "wait", "params": {
        "owner": "old", "epoch": 0, "operationId": "existing", "seconds": 1,
    }}}
    plan = {"id": "part", "title": "Lost response", "revision": 1, "steps": [step],
            "stepResults": {"1": {"invocationId": "accepted-invocation", "consumed": True}}}
    with pytest.raises(ValueError, match="Observe"):
        revise_remaining(plan, owner="new", epoch=2, material_revision=8, expected_material_revision=8,
                         replacements={1: deepcopy(step)} if explicit_replacement else None)


def test_round_revision_rejects_replacing_done_steps_in_a_completed_part():
    step = {"id": 1, "status": "done", "check": {
        "entity": "/workcell", "property": "controlSafe", "equals": True,
    }}
    plan = {"id": "part", "title": "Completed", "revision": 1, "steps": [step]}
    with pytest.raises(ValueError, match="preserved"):
        revise_round_remaining(
            [plan], owner="new", epoch=2, material_revision=8, expected_material_revision=8,
            replacements={"part": {1: deepcopy(step)}},
        )
