"""Demonstration calibration, checked against the current model by teaching."""

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=1)
def demo_calibration():
    path = Path(__file__).resolve().parents[1] / "devices/calibrations/cell-passage-demo.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value.get("schema") != "lw.cell-passage-demo-calibration/1":
        raise ValueError("Unsupported passage demonstration calibration")
    return value["locations"]


def apply_calibration(layout, descriptors):
    if not layout.get("demonstration"):
        return descriptors
    calibration = demo_calibration()
    for descriptor in descriptors:
        entry = calibration.get(descriptor["name"])
        if not entry:
            continue
        rail = entry["railPositionM"]
        descriptor["railPositionM"] = rail
        descriptor["postureContext"]["joints"][0]["position"] = rail
        for condition in descriptor["requires"]:
            if condition["entity"].endswith("/RailCarriage"):
                for operation, delta in (("gte", -.001), ("lte", .001)):
                    if operation in condition:
                        condition[operation] = rail + delta
        descriptor["gripYawOffsetRad"] = entry.get("gripYawOffsetRad", 0)
        descriptor["approachTarget"] = [
            point + offset for point, offset in zip(
                descriptor["target"], entry.get("approachOffsetM", [0, 0, .14]), strict=True
            )
        ]
    return descriptors
