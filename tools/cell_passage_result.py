"""Read-only Cell Passage material audit, separate from physical/recording qualification."""

import math
from collections import defaultdict

CELL_FIELDS = ("attachedLive", "attachedDead", "suspendedLive", "suspendedDead", "pelletLive", "pelletDead")
LIVE_FIELDS = ("attachedLive", "suspendedLive", "pelletLive")


def audit_material_result(initial, final, mapping, *, volume_tolerance=0.01, cell_tolerance=0.000001,
                          sibling_live_tolerance=1):
    """Check six mother/sample/daughter mappings against two public material snapshots.

    Includes every vessel (waste, counting and residual included) and the installed
    tip in conservation. Tolerances are inputs fixed before execution, never fitted.
    A valid material result says nothing about actual transport or incubation.
    """
    if any(not math.isfinite(value) or value < 0
           for value in (volume_tolerance, cell_tolerance, sibling_live_tolerance)):
        raise ValueError("Audit tolerances must be finite and nonnegative")
    errors, lineages = [], []

    def totals(snapshot, label):
        components, cultures = defaultdict(float), defaultdict(float)
        for row in [*snapshot["vessels"], snapshot["tip"]]:
            volume = row["volumeUl"]
            if (not math.isfinite(volume) or volume < 0
                    or volume > row.get("capacityUl", math.inf) + volume_tolerance):
                errors.append(f"{label}: invalid volume/capacity {row['id']}")
            component_volume = 0
            for component in row["components"]:
                amount = component["volumeUl"]
                if not math.isfinite(amount) or amount < 0:
                    errors.append(f"{label}: invalid component {row['id']}")
                component_volume += amount
                components[component["name"]] += amount
            if abs(component_volume - volume) > volume_tolerance:
                errors.append(f"{label}: component volume differs {row['id']}")
            for cell in row["cells"]:
                for field in CELL_FIELDS:
                    quantity = cell[field]
                    if not math.isfinite(quantity) or quantity < 0:
                        errors.append(f"{label}: invalid cells {row['id']}")
                    cultures[cell["culture"]] += quantity
        return components, cultures

    before_components, before_cells = totals(initial, "initial")
    after_components, after_cells = totals(final, "final")
    for before, after, tolerance, name in (
        (before_components, after_components, volume_tolerance, "component"),
        (before_cells, after_cells, cell_tolerance, "culture"),
    ):
        for identity in before.keys() | after.keys():
            if abs(before[identity] - after[identity]) > tolerance:
                errors.append(f"{name} not conserved: {identity}")
    if (not final["reconciled"] or final["tip"]["id"]
            or final["tip"]["volumeUl"] > volume_tolerance or final["reservations"]
            or any(row["status"] in ("running", "settling") for row in final["operations"])):
        errors.append("Unreconciled material, installed tip, reservation or operation remains")
    before = {row["id"]: row for row in initial["vessels"]}
    after = {row["id"]: row for row in final["vessels"]}
    if (len(before) != len(initial["vessels"]) or len(after) != len(final["vessels"])
            or before.keys() != after.keys()):
        errors.append("Vessel inventory changed or contains duplicate identities")
    for identity in before.keys() & after.keys():
        row, original = after[identity], before[identity]
        if original.get("waste"):
            continue
        expected_origin = original["parent"] or original["culture"]
        if any(
            cell["culture"] != expected_origin and sum(cell[field] for field in CELL_FIELDS) > cell_tolerance
            for cell in row["cells"]
        ):
            errors.append(f"Unexpected culture in vessel: {identity}")
    role_ids = [identity for row in mapping for identity in [row["mother"], row["sample"], *row["daughters"]]]
    if (len(mapping) != 6 or any(len(row["daughters"]) != 2 for row in mapping)
            or len(role_ids) != len(set(role_ids))):
        errors.append("Expected six independent mothers, six counting samples and twelve distinct daughters")
        return {"status": "invalid", "errors": errors, "lineages": []}
    origins, daughter_cultures = set(), set()

    def live(row):
        return sum(cell[field] for cell in row["cells"] for field in LIVE_FIELDS)

    for row in mapping:
        if row["mother"] not in before or any(identity not in after for identity in [
            row["sample"], *row["daughters"],
        ]):
            errors.append(f"Missing lineage vessel: {row['mother']}")
            continue
        origin = before[row["mother"]]["culture"]
        if not origin or origin in origins:
            errors.append(f"Mother culture not independent: {row['mother']}")
        origins.add(origin)
        daughters = [after[identity] for identity in row["daughters"]]
        sample = after[row["sample"]]
        for target in [sample, *daughters]:
            if any(cell["culture"] != origin for cell in target["cells"]):
                errors.append(f"Wrong sample origin: {target['id']}")
        for daughter in daughters:
            if (daughter["parent"] != origin or not daughter["culture"]
                    or daughter["culture"] in daughter_cultures
                    or daughter["culture"] == origin):
                errors.append(f"Invalid daughter lineage: {daughter['id']}")
            daughter_cultures.add(daughter["culture"])
            if abs(daughter["volumeUl"] - 1000) > volume_tolerance or live(daughter) <= 0:
                errors.append(f"Invalid daughter yield/volume: {daughter['id']}")
        if abs(sample["volumeUl"] - 20) > volume_tolerance or live(sample) <= 0:
            errors.append(f"Invalid counting sample: {sample['id']}")
        yields = [live(daughter) for daughter in daughters]
        if abs(yields[0] - yields[1]) > sibling_live_tolerance:
            errors.append(f"Unequal daughter allocation: {row['mother']}")
        lineages.append({"mother": row["mother"], "origin": origin,
                         "sampleLive": live(sample), "daughterLive": yields})
    return {"status": "invalid" if errors else "material_valid", "errors": errors, "lineages": lineages}
