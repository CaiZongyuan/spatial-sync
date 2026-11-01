"""One geometry-derived location/conditional-FK definition for binding and teaching."""

import math

from factory.cell_passage import HANDLE, LAB


def locations(layout):
    stations, result = layout["stations"], []
    yaws = layout.get("stationYawRad", {})

    def add(name, site, point, offset, *, yaw=0.0, requires=(), body=None, zone=None,
            reference_site=None, target_offset=None):
        leaf = name.split(".", 1)[0]
        rail = (
            -0.45
            if leaf.startswith("Incubator")
            else 0.4
            if leaf.startswith("Flex")
            else float(min(0.45, max(-0.5, point[0] - 0.2)))
        )
        if "/Centrifuge/" in site:
            rail = -0.45
        rules = [
            {"entity": LAB + "/RailCarriage", "property": "position", "gte": rail - 0.001},
            {"entity": LAB + "/RailCarriage", "property": "position", "lte": rail + 0.001},
        ]
        if leaf.startswith("Incubator"):
            rules.extend(
                {"entity": LAB + "/Incubator/" + door, "property": "opened", "equals": True}
                for door in ("OuterDoor", "InnerDoor")
            )
        if "/Centrifuge/" in site:
            rules.extend(
                [
                    {"entity": LAB + "/Centrifuge", "property": "lidOpen", "equals": True},
                    {"entity": LAB + "/Centrifuge", "property": "phase", "equals": "idle"},
                ]
            )
        rules.extend(requires)
        c, s = math.cos(yaw), math.sin(yaw)
        offset_world = [c * offset[0] - s * offset[1], s * offset[0] + c * offset[1], offset[2]]
        target = [point[i] + offset_world[i] for i in range(3)]
        approach = [target[0], target[1], target[2] + 0.14]
        if leaf.startswith("Incubator"):
            approach[1] = -0.05
        result.append(
            {
                "name": name,
                "site": site,
                "target": target,
                "approachTarget": approach,
                "yawRad": yaw,
                "railPositionM": rail,
                "postureContext": {
                    "site": reference_site or site,
                    "offsetM": list(offset if target_offset is None else target_offset),
                    "joints": [{"joint": LAB + "/Joints/RailCarriage", "position": rail}],
                },
                "requires": rules,
                "body": body,
                "zone": zone or ("centrifuge" if "/Centrifuge/" in site else leaf),
            }
        )
        if "/Centrifuge/" in site and layout.get("demonstration"):
            result[-1]["postureContext"]["joints"].append({
                "joint": LAB + "/Centrifuge/LidHinge", "position": layout["centrifugeLidOpenRad"],
            })

    for key, point in stations.items():
        name = key.rsplit("/", 1)[1]
        if layout.get("demonstration") and name in ("FlexTube7", "FlexTube8"):
            continue  # Physical stations remain, outside this demonstration's taught scope.
        if name.endswith("Transit"):
            add(name + ".transit", key, point, [0, 0, 0], yaw=yaws.get(key, 0))
            result[-1]["approachTarget"] = list(point)
        elif name.startswith(("Rack", "FlexTube")):
            add(name + ".tube", key, point, [0.035, 0, 0.038])
        elif name.startswith("Lid"):
            number = int(name[3:])
            offset = [-HANDLE[0], 0, HANDLE[2]] if number <= 4 else [-0.035, 0, 0.022]
            add(name + (".plateLid" if number <= 4 else ".cap"), key, point, offset)
        else:
            add(name + ".plate", key, point, HANDLE, yaw=yaws.get(key, 0))
    for number, key in enumerate(layout["slotSites"], 1):
        offset = ((0.12, 0), (0, 0.12), (-0.12, 0), (0, -0.12))[number - 1]
        add(
            f"CentrifugeSlot{number}.tube",
            key,
            [-0.53 + offset[0], -0.65 + offset[1], 0.999],
            [0.035, 0, 0.038],
        )
    for name, body in layout["bodies"].items():
        if body not in layout["lids"]:
            continue
        if name in ("Mother", "DaughterA", "DaughterB", "Counting"):
            candidates = [("FlexPlate", 0.018)]
            offset = [-HANDLE[0], 0, HANDLE[2]]
        else:
            index = [f"S{i}" for i in range(1, 7)] + ["B5", "B6"]
            candidates = [("Rack" + name, 0.05), (f"FlexTube{index.index(name) + 1}", 0.05)]
            offset = [-0.035, 0, 0.022]
        for station, height in candidates:
            if layout.get("demonstration") and station in ("FlexTube7", "FlexTube8"):
                continue
            key = LAB + "/Sites/" + station
            point = [*stations[key][:2], stations[key][2] + height]
            add(
                station + "." + name + "Lid",
                body + "/LidSeat",
                point,
                offset,
                requires=[{"entity": body, "property": "station", "equals": key}],
                body=layout["lids"][body],
                reference_site=key,
                target_offset=[offset[0], offset[1], offset[2] + height],
            )
    from factory.cell_passage_calibration import apply_calibration

    return apply_calibration(layout, result)

