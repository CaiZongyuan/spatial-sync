"""Ordered real-Program path expansion and assembly consumers, without a solver."""

import pytest

from tools.cell_passage_plan import ROBOT
from tools.cell_passage_sweep import MotionLedger, interpolation


def ledger():
    tube, cap = "/Lab/Tube", "/Lab/Cap"
    layout = {"initialStations": {tube: "/Lab/Rack", cap: tube + "/LidSeat"}, "lids": {tube: cap}}
    locations = {
        "rack": {"site": "/Lab/Rack", "approach": [0.0] * 7, "preInsertion": [0.1] * 7, "grasp": [0.2] * 7},
        "deck": {"site": "/Lab/Deck", "approach": [0.3] * 7, "grasp": [0.4] * 7},
        "cap": {"site": tube + "/LidSeat", "approach": [0.5] * 7, "grasp": [0.6] * 7},
    }
    bindings = {
        "entities": [
            {
                "key": ROBOT,
                "config": {
                    "tubes": [tube, cap],
                    "attachedLids": {tube: cap},
                    "speedScale": 0.3,
                    "jointAccelerationRadPerS2": 0.5,
                    "symmetricPreInsertionLocations": ["rack"],
                },
            }
        ]
    }
    return MotionLedger(layout, locations, bindings, arm_initial=[0.0] * 7)


def action(model, i, name, location):
    return model.advance(
        {"id": i, "do": {"entity": ROBOT, "action": name, "params": {"location": location}}}, stage=1, part=1
    )


def test_runtime_symmetric_path_and_complete_carried_assembly():
    model = ledger()
    row = action(model, 1, "pick", "rack")
    moves = [s for s in row["segments"] if s["kind"] == "arm"]
    assert [s["end"][0] for s in moves] == [0.0, 0.1, 0.2, 0.1, 0.0]
    assert moves[2]["assembly"] == []
    assert moves[3]["assembly"] == ["/Lab/Tube", "/Lab/Cap"]
    assert row["assemblyAfter"] == ["/Lab/Tube", "/Lab/Cap"]
    placed = action(model, 2, "place", "deck")
    assert placed["heldAfter"] is None
    assert model.placements == {"/Lab/Tube": "/Lab/Deck", "/Lab/Cap": "/Lab/Tube/LidSeat"}
    picked_cap = action(model, 3, "pick", "cap")
    assert picked_cap["assemblyAfter"] == ["/Lab/Cap"]
    assert picked_cap["placementsBefore"]["/Lab/Tube"] == "/Lab/Deck"


def test_interpolation_matches_independent_quintic_and_trapezoid_midpoints():
    assert interpolation(
        {"start": [0, 0, 0], "end": [1, 2, 3], "duration": 1, "interpolation": "flex-quintic"}, 0.25
    ) == pytest.approx([0.103515625, 0.20703125, 0.310546875])
    segment = {
        "start": [0.0] * 7,
        "end": [1.0] * 7,
        "duration": 2**0.5 * 2,
        "interpolation": "arm-trapezoid",
        "speedScale": 0.3,
        "speed": 1,
        "acceleration": 0.5,
    }
    assert interpolation(segment, 2**0.5)[0] == pytest.approx(0.5)


def test_missing_initial_state_stays_an_explicit_gap():
    model = ledger()
    model.arm = None
    row = action(model, 1, "pick", "rack")
    assert "model_initial_arm_required" in row["gaps"]
    assert row["verification"] == "pending-geometry"
