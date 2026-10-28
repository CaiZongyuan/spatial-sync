"""Mechanical bindings only; material and control configuration is supplied externally."""

from factory.cell_passage import LAB, site


def bindings(stage, layout):
    targets, limits = layout["environmentTargets"], layout["environmentLimits"]
    robot = LAB + "/Robot/fr3_link0"
    tubes = [
        key
        for name, key in layout["bodies"].items()
        if name.startswith("S") and name != "SolidWaste" or name in ("B5", "B6")
    ]
    movable = list(layout["graspOffsets"])
    keys = layout["bodies"]
    board_sites = [
        key for key in layout["stations"] if not key.rsplit("/", 1)[1].startswith(("Lid", "Rack", "FlexTube"))
    ]
    tube_sites = [key for key in layout["stations"] if key.rsplit("/", 1)[1].startswith(("Rack", "FlexTube"))]
    lid_sites = [key for key in layout["stations"] if key.rsplit("/", 1)[1].startswith("Lid")]
    tube_sites += layout["slotSites"]
    grip_shapes = {key: key + "/Handle" for key in movable}
    entities = [
        {
            "key": robot,
            "type": "labware-motion/v1",
            "program": "labware.motion",
            "config": {
                "locations": "locations.yaml",
                "controlEntity": LAB + "/LiquidHandler",
                "tubes": movable,
                "graspOffsets": layout["graspOffsets"],
                "gripDiameters": {key: 0.017 for key in movable},
                "gripShapePrefixes": grip_shapes,
                "jointKeys": [LAB + f"/Robot/Joints/fr3_joint{i}" for i in range(1, 8)],
                "fingerKeys": [LAB + f"/Robot/Joints/fr3_finger_joint{i}" for i in (1, 2)],
                "fingerPadShapes": [
                    LAB + "/Robot/fr3_" + name + "finger/Collision_3" for name in ("left", "right")
                ],
                "handBody": LAB + "/Robot/fr3_hand",
                "tcpSite": LAB + "/Robot/fr3_hand/Frames/fr3_hand_tcp",
                "speedScale": 0.3,
                "graspMode": "weld",
            },
        }
    ]
    for name, body in keys.items():
        if body not in layout["lids"]:
            continue
        is_tube = body in tubes
        seat = body + "/LidSeat"
        site(stage, seat, (0, 0, 0.05 if is_tube else 0.018))
        lid = layout["lids"][body]
        entities.extend(
            [
                {
                    "key": body,
                    "type": "labware/v1",
                    "program": "labware.facts",
                    "config": {
                        "robots": [robot],
                        "locationSites": tube_sites if is_tube else board_sites,
                        "lidBody": lid,
                        "lidOffsetM": 0.05 if is_tube else 0.018,
                    },
                },
                {
                    "key": lid,
                    "type": "labware/v1",
                    "program": "labware.facts",
                    "config": {"robots": [robot], "locationSites": [*lid_sites, seat]},
                },
            ]
        )
    for name, role, peer in (("OuterDoor", "outer", "InnerDoor"), ("InnerDoor", "inner", "OuterDoor")):
        entities.append(
            {
                "key": LAB + "/Incubator/" + name,
                "type": "passage-joint/v1",
                "program": "cell_passage.joint",
                "config": {
                    "controlEntity": LAB + "/LiquidHandler",
                    "jointKey": LAB + "/Joints/Incubator" + name,
                    "lower": 0.0,
                    "upper": 1.745,
                    "maxSpeed": 0.4,
                    "tolerance": 0.005,
                    "role": role,
                    "peerDoor": LAB + "/Incubator/" + peer,
                },
            }
        )
    entities.append(
        {
            "key": LAB + "/RailCarriage",
            "type": "passage-joint/v1",
            "program": "cell_passage.joint",
            "config": {
                "controlEntity": LAB + "/LiquidHandler",
                "jointKey": LAB + "/Joints/RailCarriage",
                "lower": -0.5,
                "upper": 0.45,
                "maxSpeed": 0.08,
                "role": "axis",
            },
        }
    )
    entities.append(
        {
            "key": LAB + "/Centrifuge",
            "type": "passage-centrifuge/v1",
            "program": "cell_passage.centrifuge",
            "config": {
                "controlEntity": LAB + "/LiquidHandler",
                "slotSites": layout["slotSites"],
                "housingBody": LAB + "/Centrifuge/Housing",
                "armBodies": [LAB + f"/Robot/fr3_link{i}" for i in range(8)]
                + [LAB + "/Robot/fr3_hand", LAB + "/Robot/fr3_leftfinger", LAB + "/Robot/fr3_rightfinger"],
                "tubes": tubes,
                "spindleJoint": LAB + "/Centrifuge/Spindle",
                "lidJoint": LAB + "/Centrifuge/LidHinge",
                "tareKg": {key: 0.016 for key in tubes},
                "visualRpmCap": 60,
                "densityKgPerL": 1.0,
                "balanceToleranceKg": 0.00002,
            },
        }
    )
    entities.extend(
        [
            {
                "key": LAB + "/Incubator",
                "type": "passage-incubator/v1",
                "program": "cell_passage.incubator",
                "config": {
                    "controlEntity": LAB + "/LiquidHandler",
                    "outerDoorJoint": LAB + "/Joints/IncubatorOuterDoor",
                    "innerDoorJoint": LAB + "/Joints/IncubatorInnerDoor",
                    "initialTemperatureC": targets["temperatureC"],
                    "setpointC": targets["temperatureC"],
                    "initialCo2Percent": targets["co2Percent"],
                    "initialHumidityPercent": targets["humidityPercent"],
                    "targetHumidityPercent": targets["humidityPercent"],
                    "targetCo2Percent": targets["co2Percent"],
                    "temperatureRangeC": limits["temperatureC"],
                    "co2RangePercent": limits["co2Percent"],
                    "humidityRangePercent": limits["humidityPercent"],
                    "timeConstantSeconds": 10,
                },
            },
            {
                "key": LAB + "/LiquidHandler/Base",
                "type": "passage-cabinet/v1",
                "program": "cell_passage.cabinet",
                "config": {
                    "controlEntity": LAB + "/LiquidHandler",
                    "initialInflowMs": 0.45,
                    "blowerOn": True,
                    "uvOn": False,
                    "sashHeightCm": 20,
                },
            },
        ]
    )
    return {"scene": "cell-passage", "entities": entities}
