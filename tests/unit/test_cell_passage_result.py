"""Independent worked material example; this does not establish physical completion."""

from copy import deepcopy

from tools.cell_passage_result import audit_material_result


def cell(culture, live):
    return {"culture": culture, "parent": "", "attachedLive": 0, "attachedDead": 0,
            "suspendedLive": live, "suspendedDead": 0, "pelletLive": 0, "pelletDead": 0}


def vessel(identity, volume, *, culture="", parent="", live=0):
    return {"id": identity, "volumeUl": volume, "capacityUl": 10000,
            "components": [{"name": "medium", "volumeUl": volume}],
            "culture": culture, "parent": parent, "cells": [cell(parent or culture, live)] if live else []}


def worked_example():
    initial = {"reconciled": True, "vessels": [vessel("medium", 6120)],
               "tip": {"id": "", "volumeUl": 0, "components": [], "cells": []},
               "reservations": [], "operations": []}
    final = deepcopy(initial)
    final["vessels"] = [vessel("medium", 0)]
    mapping = []
    for number in range(1, 7):
        mother, culture = f"M{number}", f"culture-{number}"
        initial["vessels"].append(vessel(mother, 1000, culture=culture, live=100))
        initial["vessels"].extend([
            vessel(f"C{number}", 0, culture=f"counting-{number}", parent=culture),
            vessel(f"A{number}", 0, culture=f"daughter-a-{number}", parent=culture),
            vessel(f"B{number}", 0, culture=f"daughter-b-{number}", parent=culture),
        ])
        final["vessels"].append(vessel(mother, 0, culture=culture))
        final["vessels"].append(vessel(
            f"C{number}", 20, culture=f"counting-{number}", parent=culture, live=2,
        ))
        final["vessels"].extend([
            vessel(f"A{number}", 1000, culture=f"daughter-a-{number}", parent=culture, live=49),
            vessel(f"B{number}", 1000, culture=f"daughter-b-{number}", parent=culture, live=49),
        ])
        mapping.append({"mother": mother, "sample": f"C{number}", "daughters": [f"A{number}", f"B{number}"]})
    return initial, final, mapping


def test_six_independent_samples_are_conserved_and_wrong_source_daughter_is_rejected():
    initial, final, mapping = worked_example()
    result = audit_material_result(initial, final, mapping)
    assert result["status"] == "material_valid"
    assert len(result["lineages"]) == 6
    assert result["lineages"][0]["daughterLive"] == [49, 49]
    wrong = deepcopy(final)
    a1 = next(row for row in wrong["vessels"] if row["id"] == "A1")
    a2 = next(row for row in wrong["vessels"] if row["id"] == "A2")
    a1["cells"], a2["cells"] = a2["cells"], a1["cells"]
    assert audit_material_result(initial, wrong, mapping)["status"] == "invalid"


def test_conservation_alone_does_not_allow_cells_in_common_reagent():
    initial, final, mapping = worked_example()
    next(row for row in final["vessels"] if row["id"] == "medium")["cells"] = [cell("culture-1", 1)]
    for identity in ("A1", "B1"):
        next(row for row in final["vessels"] if row["id"] == identity)["cells"][0]["suspendedLive"] = 48.5
    assert audit_material_result(initial, final, mapping)["status"] == "invalid"


def test_sibling_live_tolerance_is_separate_from_strict_global_conservation():
    initial, final, mapping = worked_example()
    a = next(row for row in final["vessels"] if row["id"] == "A1")
    b = next(row for row in final["vessels"] if row["id"] == "B1")
    a["cells"][0]["suspendedLive"], b["cells"][0]["suspendedLive"] = 49.4, 48.6
    assert audit_material_result(initial, final, mapping)["status"] == "material_valid"
    a["cells"][0]["suspendedLive"], b["cells"][0]["suspendedLive"] = 49.6, 48.4
    assert audit_material_result(initial, final, mapping)["status"] == "invalid"
    a["cells"][0]["suspendedLive"], b["cells"][0]["suspendedLive"] = 49.0001, 49
    assert audit_material_result(initial, final, mapping)["status"] == "invalid"
