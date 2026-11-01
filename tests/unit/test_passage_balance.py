import pytest

from packages.programs.labware.centrifuge import modeled_slot_masses


def test_four_slot_mass_uses_current_material_and_tare_not_only_occupancy():
    rows = [{"entityKey": "S1", "volumeUl": 1000}, {"entityKey": "S2", "volumeUl": 900}]
    masses = modeled_slot_masses(["S1", None, "S2", None], rows, {"S1": 0.012, "S2": 0.012}, 1)
    assert masses == pytest.approx([0.013, 0, 0.0129, 0])
    assert abs(masses[0] - masses[2]) > 0.00002


def test_unknown_or_duplicate_material_cannot_prove_balance():
    with pytest.raises(ValueError):
        modeled_slot_masses(["S1", None, None, None], [], {"S1": 0.012}, 1)
    rows = [{"entityKey": "S1", "volumeUl": 1000}] * 2
    with pytest.raises(ValueError):
        modeled_slot_masses(["S1", None, None, None], rows, {"S1": 0.012}, 1)
