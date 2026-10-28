"""Build the workcell binding from the Scene Factory's mechanical-layout receipt.

This writes declarative configuration only; it neither imports the factory nor
claims that the resulting scene has passed mechanical qualification.
"""

import argparse
import json
from pathlib import Path

import yaml

from .workcell import WorkcellProgram

LAB = "/World/Lab"
WELLS = ("A1", "A2", "A3", "B1", "B2", "B3")


def workcell_binding(layout):
    bodies, vessels = layout["bodies"], []
    for index, well in enumerate(WELLS, 1):
        origin = f"M{index}"
        for plate in ("Mother", "DaughterA", "DaughterB", "Counting"):
            mother = plate == "Mother"
            vessels.append(
                {
                    "id": f"{plate}/{well}",
                    "entityKey": bodies[plate],
                    "site": f"{bodies[plate]}/{well}",
                    "capacityUl": 3000,
                    "volumeUl": 1000 if mother else 0,
                    "culture": origin if mother else f"{plate}{index}",
                    "parent": "" if mother else origin,
                    "live": 600000 if mother else 0,
                    "dead": 30000 if mother else 0,
                    "attached": mother,
                    "lidProperty": "lidClosed",
                    "requiredStation": LAB + "/Sites/FlexPlate",
                }
            )
        vessels.append(
            {
                "id": f"S{index}",
                "entityKey": bodies[f"S{index}"],
                "site": bodies[f"S{index}"] + "/Content",
                "capacityUl": 2000,
                "parent": origin,
                "lidProperty": "lidClosed",
                "requiredStation": LAB + f"/Sites/FlexTube{index}",
            }
        )
    for index in (5, 6):
        name = f"B{index}"
        vessels.append(
            {
                "id": name,
                "entityKey": bodies[name],
                "site": bodies[name] + "/Content",
                "capacityUl": 2000,
                "volumeUl": 1000,
                "reagent": "balance-water",
                "lidProperty": "lidClosed",
                "requiredStation": LAB + f"/Sites/FlexTube{index + 2}",
            }
        )
    for name, reagent, volume in (
        ("ReservoirPBS", "PBS", 6000),
        ("ReservoirTrypsin", "trypsin", 6000),
        ("ReservoirMedium", "medium", 25000),
    ):
        vessels.append(
            {
                "id": name,
                "entityKey": bodies[name],
                "site": bodies[name] + "/Content",
                "capacityUl": 50000,
                "volumeUl": volume,
                "reagent": reagent,
            }
        )
    for name in ("WasteLiquid", "SolidWaste"):
        vessels.append(
            {
                "id": name,
                "entityKey": bodies[name],
                "site": bodies[name] + "/Content",
                "capacityUl": 50000,
                "waste": True,
            }
        )
    config = {name: layout[name] for name in ("axisJoints", "pumpJoint", "tipSite", "pipetteBody", "tips")}
    config.update(
        contentSites=sorted({row["site"] for row in vessels}),
        tipRackSites=sorted({row["site"] for row in layout["tips"]}),
        vessels=vessels,
        pumpUlPerUnit=1000 / layout["pumpStrokeM"],
        alignmentToleranceM=layout.get("pipetteCalibration", {}).get("alignmentToleranceM", 0.003),
        managedDevices=[
            LAB + "/Robot/fr3_link0",
            LAB + "/RailCarriage",
            LAB + "/Incubator/OuterDoor",
            LAB + "/Incubator/InnerDoor",
            LAB + "/Centrifuge",
            LAB + "/Incubator",
            LAB + "/LiquidHandler/Base",
        ],
        cabinetEntity=LAB + "/LiquidHandler/Base",
        imagerStation=LAB + "/Sites/Imager",
        wasteSite=layout.get("tipDiscardSite", bodies["SolidWaste"] + "/Content"),
    )
    parsed = WorkcellProgram.Config.model_validate(config)
    return {
        "key": LAB + "/LiquidHandler",
        "type": WorkcellProgram.type,
        "program": WorkcellProgram.id,
        "config": parsed.model_dump(mode="json"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    layout = json.loads(args.layout.read_text(encoding="utf8"))
    binding = workcell_binding(layout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        yaml.safe_dump({"scene": "cell-passage", "entities": [binding]}, sort_keys=False), encoding="utf8"
    )
    print(
        json.dumps(
            {
                "output": str(args.out),
                "vessels": len(binding["config"]["vessels"]),
                "tips": len(binding["config"]["tips"]),
                "mechanicallyQualified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
