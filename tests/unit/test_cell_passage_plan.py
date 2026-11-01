"""Protocol-derived independent expectations over a synthetic structural scene fixture."""

from collections import Counter

import pytest

from packages.programs.cell_passage.scene import WELLS, workcell_binding
from packages.protocol.schema import OperationPlan
from tests.fixtures.cell_passage import build_workcell_host
from tools.cell_passage_plan import compile_recipe, paginate_recipe


def recipe_inputs():
    lab = "/World/Lab"
    names = ["Mother", "DaughterA", "DaughterB", "Counting", *[f"S{i}" for i in range(1, 7)], "B5", "B6"]
    bodies = {name: lab + "/Labware/" + name for name in [
        *names, "ReservoirPBS", "ReservoirTrypsin", "ReservoirMedium", "WasteLiquid", "SolidWaste",
    ]}
    stations = {lab + "/Sites/" + name: [0.59, 0.29, 1.032] for name in [
        "IncubatorMother", "IncubatorA", "IncubatorB", "Imager", "FlexPlate", "DaughterAReady",
        "DaughterBReady", "CountingReady", "WasteCounting", "WasteMother",
        *["Rack" + name for name in names[4:]], *[f"FlexTube{i}" for i in range(1, 9)],
        *[f"Lid{i}" for i in range(1, 13)],
    ]}
    starts = {"Mother": "IncubatorMother", "DaughterA": "DaughterAReady",
              "DaughterB": "DaughterBReady", "Counting": "CountingReady"}
    lids = {bodies[name]: bodies[name] + "Lid" for name in names}
    layout = {"schema": "lw.cell-passage-layout/1", "mechanicallyQualified": False,
              "bodies": bodies, "stations": stations, "lids": lids,
              "initialStations": {bodies[name]: lab + "/Sites/" + starts.get(name, "Rack" + name)
                                  for name in names},
              "lidParking": {lids[bodies[name]]: lab + f"/Sites/Lid{i}" for i, name in enumerate(names, 1)},
              "slotSites": [lab + f"/Centrifuge/Rotor/Slot{i}" for i in range(1, 5)],
              "axisJoints": ["x", "y", "z"], "pumpJoint": "pump", "tipSite": "tip",
              "pipetteBody": "pipette", "pumpStrokeM": 0.01,
              "tips": [{"id": f"T{i}", "site": f"/rack/T{i}", "bodyKey": f"/tips/T{i}"}
                       for i in range(1, 97)],
              "pipetteCalibration": {"tipOriginM": [0.7, 0.4, 1.034],
                                     "axisDirections": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                     "qLowerM": [-0.24, -0.21, 0], "qUpperM": [0.12, 0.21, 0.08]}}
    layout["initialStations"].update({lid: body + "/LidSeat" for body, lid in lids.items()})
    binding = workcell_binding(layout)
    descriptors = []
    for site in [*stations, *layout["slotSites"]]:
        descriptors.append({"name": site.rsplit("/", 1)[1], "site": site,
                            "railPositionM": 0, "requires": [], "body": None, "valid": True})
    for name in names:
        sites = (["FlexPlate"] if name in names[:4]
                 else ["Rack" + name, f"FlexTube{names[4:].index(name) + 1}"])
        for station in sites:
            descriptors.append({"name": station + "." + name + "Lid", "site": bodies[name] + "/LidSeat",
                                "body": lids[bodies[name]], "railPositionM": 0, "valid": True,
                                "requires": [{"entity": bodies[name], "property": "station",
                                              "equals": lab + "/Sites/" + station}]})
    geometry = {"localContents": {},
                "staticSites": {tip["site"]: [0.7, 0.5, 1.034] for tip in layout["tips"]}}
    for row in binding["config"]["vessels"]:
        if "/" in row["id"]:
            well = row["id"].split("/")[1]
            index = WELLS.index(well)
            geometry["localContents"][row["id"]] = [(index % 3 - 1) * 0.039,
                                                   -0.0195 if index < 3 else 0.0195, 0.002]
        else:
            geometry["localContents"][row["id"]] = [0, 0, 0.003]
        if row["entityKey"] not in layout["initialStations"]:
            geometry["staticSites"][row["site"]] = [0.7, 0.4, 1.034]
    teaching = {"schema": "lw.cell-passage-teaching/1", "failed": [], "locations": descriptors}
    return layout, teaching, binding, geometry


def test_complete_recipe_has_independent_samples_two_balanced_batches_and_84_fresh_tips(tmp_path):
    recipe = compile_recipe(*recipe_inputs(), owner="auto", epoch=0, round_id="fixture")
    assert {row["stage"] for row in recipe["plans"]} == set(range(1, 8))
    assert recipe["mechanicallyQualified"] is False
    assert max(len(row["document"]["steps"]) for row in recipe["plans"]) <= 128
    steps = [step for plan in recipe["plans"] for step in plan["document"]["steps"]]
    calls = [step["do"] for step in steps if "do" in step]
    counts = Counter(call["action"] for call in calls)
    assert counts["pickupTip"] == counts["discardTip"] == 84
    assert counts["observe"] == counts["count"] == 6
    assert counts["aspirate"] == 18
    assert counts["aspirateAvailable"] == 30 and counts["aspirateFraction"] == 6
    assert counts["aspirateTopUp"] == 24 and counts["dispenseAll"] == 78
    assert counts["dispense"] == 0
    assert {call["params"]["volumeUl"] for call in calls if call["action"] == "mix"} == {900}
    pellets = [call["params"]["vessels"] for call in calls if call["action"] == "pellet"]
    assert pellets == [["S1", "S2", "S3", "S4"], ["S5", "S6"]]
    assert len({call["params"]["operationId"] for call in calls}) == len(calls)
    host, _ = build_workcell_host(tmp_path)
    for call in calls:
        if call["entity"] == "/World/Lab/LiquidHandler":
            host.catalog.validate_params("/workcell", call["action"], call["params"])
    for stage in recipe["plans"]:
        OperationPlan.model_validate({"id": "fixture", "revision": 1, **stage["document"]})
        assert all(step["id"] // 100000 == stage["stage"] for step in stage["document"]["steps"])


def test_environment_uses_controlled_simulation_wait_and_public_final_ranges():
    layout, teaching, binding, geometry = recipe_inputs()
    layout.update(incubatorEntity="/World/Lab/Incubator", cabinetEntity="/World/Lab/LiquidHandler/Base",
                  environmentLimits={"temperatureC": [36.5, 37.5], "co2Percent": [4.5, 5.5],
                                     "humidityPercent": [90, 100], "inflowMs": [0.4, 2],
                                     "sashHeightCm": [18, 22]})
    binding["config"]["managedDevices"].extend([layout["incubatorEntity"], layout["cabinetEntity"]])
    recipe = compile_recipe(
        layout, teaching, binding, geometry, owner="auto", epoch=0, round_id="environment"
    )
    final = [step for row in recipe["plans"] if row["stage"] == 7 for step in row["document"]["steps"]]
    assert recipe["environmentChecksAvailable"] is True
    waits = [step["do"] for step in final if step.get("do", {}).get("action") == "waitReady"]
    assert len(waits) == 1 and waits[0]["entity"] == "/World/Lab/Incubator"
    assert waits[0]["params"]["timeoutSeconds"] == 90
    conditions = [step["check"] for step in final if "check" in step]
    assert {"entity": "/World/Lab/Incubator", "property": "temperatureC", "gte": 36.5} in conditions
    assert {"entity": "/World/Lab/Incubator", "property": "temperatureC", "lte": 37.5} in conditions


def test_both_incubator_returns_retract_the_arm_and_rail_before_closing():
    recipe = compile_recipe(*recipe_inputs(), owner="auto", epoch=0, round_id="door-clearance")
    for stage in (1, 7):
        calls = [step["do"] for row in recipe["plans"] if row["stage"] == stage
                 for step in row["document"]["steps"] if "do" in step]
        close = next(index for index, call in enumerate(calls)
                     if call["entity"] == "/World/Lab/Incubator/InnerDoor" and call["action"] == "close")
        assert (calls[close - 2]["entity"], calls[close - 2]["action"]) == (
            "/World/Lab/Robot/fr3_link0", "home")
        assert (calls[close - 1]["entity"], calls[close - 1]["action"],
                calls[close - 1]["params"]["position"]) == ("/World/Lab/RailCarriage", "move", .4)


def test_page_boundaries_preserve_complete_action_order_and_global_identity():
    unpaged = compile_recipe(*recipe_inputs(), owner="auto", epoch=0, round_id="paging", max_steps=None)
    paged = paginate_recipe(unpaged, max_steps=128)
    assert [step for row in paged["plans"] for step in row["document"]["steps"]] == [
        step for row in unpaged["plans"] for step in row["document"]["steps"]
    ]
    for stage in range(1, 8):
        parts = [row for row in paged["plans"] if row["stage"] == stage]
        assert [row["part"] for row in parts] == list(range(1, len(parts) + 1))
        assert all(row["parts"] == len(parts) for row in parts)


def test_transport_waypoints_enter_forward_exit_reverse_and_retain_payload_leases():
    layout, teaching, binding, geometry = recipe_inputs()
    source, destination = "IncubatorMother", "Imager"
    entry, landing = [[.1] * 7, [.2] * 7], [[.3] * 7, [.4] * 7]
    layout["transportWaypoints"] = {source: entry, destination: landing}
    for row in teaching["locations"]:
        if row["name"] in (source, destination):
            row["railPositionM"] = -.4 if row["name"] == source else .3
    recipe = compile_recipe(layout, teaching, binding, geometry,
                            owner="auto", epoch=0, round_id="waypoints", max_steps=None)
    steps = recipe["plans"][0]["document"]["steps"]
    calls = [step["do"] for step in steps if "do" in step]
    pick = next(i for i, c in enumerate(calls) if c["action"] == "pick")
    place = next(i for i, c in enumerate(calls) if c["action"] == "place")
    assert [c["params"]["positions"] for c in calls[pick - 2:pick]] == entry
    assert [c["params"]["positions"] for c in calls[pick + 1:pick + 3]] == entry[::-1]
    assert [c["action"] for c in calls[pick + 3:pick + 5]] == ["home", "move"]
    assert [c["params"]["positions"] for c in calls[place - 2:place]] == landing
    assert [c["params"]["positions"] for c in calls[place + 1:place + 3]] == landing[::-1]
    assert all(c["entity"] == "/World/Lab/Robot/fr3_link0" and c["action"] == "moveJoints"
               for c in [*calls[pick - 2:pick], *calls[pick + 1:pick + 3],
                         *calls[place - 2:place], *calls[place + 1:place + 3]])
    leases = {}
    for call in calls[:place + 3]:
        params = call["params"]
        if call["action"] == "reserve":
            leases[params["resource"]] = params["device"]
        elif call["action"] == "release":
            leases.pop(params["resource"])
        if call["action"] in ("pick", "place", "moveJoints"):
            assert leases[layout["bodies"]["Mother"]] == "/World/Lab/Robot/fr3_link0"
            assert leases["/World/Lab/Sites/IncubatorMother"] == "/World/Lab/Robot/fr3_link0"
            assert leases["/World/Lab/Sites/Imager"] == "/World/Lab/Robot/fr3_link0"


def test_incubator_single_transit_remains_before_and_after_pick():
    layout, teaching, binding, geometry = recipe_inputs()
    layout["incubatorTransitLocations"] = {"IncubatorMother": "IncubatorEntry"}
    teaching["locations"].append({"name": "IncubatorEntry", "site": "/World/Lab/Sites/Entry",
                                  "railPositionM": 0, "requires": [], "body": None, "valid": True})
    recipe = compile_recipe(layout, teaching, binding, geometry,
                            owner="auto", epoch=0, round_id="transit", max_steps=None)
    calls = [s["do"] for s in recipe["plans"][0]["document"]["steps"] if "do" in s]
    pick = next(i for i, c in enumerate(calls) if c["action"] == "pick")
    for call in (calls[pick - 1], calls[pick + 1]):
        assert call["action"] == "moveTo"
        assert call["params"]["location"] == "IncubatorEntry"
        assert call["params"]["pose"] == "approach"
    assert any(c["action"] == "reserve" and c["params"]["resource"] == "/World/Lab/Sites/Entry"
               for c in calls[:pick])
    assert any(c["action"] == "release" and c["params"]["resource"] == "/World/Lab/Sites/Entry"
               for c in calls[pick + 1:])


@pytest.mark.parametrize("points", [None, {}, [1], [[0] * 6], [[0] * 8],
                                      [[True] * 7], [[float("nan")] * 7], [[float("inf")] * 7],
                                      [["0"] * 7]])
def test_transport_waypoints_reject_malformed_joint_vectors(points):
    layout, teaching, binding, geometry = recipe_inputs()
    layout["transportWaypoints"] = {"Imager": points}
    with pytest.raises(ValueError, match="seven finite, nonboolean"):
        compile_recipe(layout, teaching, binding, geometry, owner="auto", epoch=0, round_id="invalid")


def test_transport_waypoints_reject_duplicate_incubator_route_configuration():
    layout, teaching, binding, geometry = recipe_inputs()
    layout["transportWaypoints"] = {"IncubatorMother": [[0] * 7]}
    layout["incubatorTransitLocations"] = {"IncubatorMother": "IncubatorEntry"}
    with pytest.raises(ValueError, match="overlap an incubator transit"):
        compile_recipe(layout, teaching, binding, geometry, owner="auto", epoch=0, round_id="overlap")


def test_optional_centrifuge_lid_self_check_follows_retraction_and_both_closed_doors():
    layout, teaching, binding, geometry = recipe_inputs()
    layout["centrifugeLidSelfCheck"] = True
    recipe = compile_recipe(layout, teaching, binding, geometry,
                            owner="auto", epoch=0, round_id="self-check", max_steps=None)
    calls = [s["do"] for s in recipe["plans"][0]["document"]["steps"] if "do" in s]
    assert [(c["entity"], c["action"]) for c in calls[-6:]] == [
        ("/World/Lab/Robot/fr3_link0", "home"), ("/World/Lab/RailCarriage", "move"),
        ("/World/Lab/Incubator/InnerDoor", "close"), ("/World/Lab/Incubator/OuterDoor", "close"),
        ("/World/Lab/Centrifuge", "openLid"), ("/World/Lab/Centrifuge", "closeLid"),
    ]
    assert calls[-5]["params"]["position"] == .4
    leases = {}
    for call in calls:
        params = call["params"]
        if call["action"] == "reserve":
            leases[params["resource"]] = params["device"]
        elif call["action"] == "release":
            leases.pop(params["resource"])
        if call["entity"] == "/World/Lab/Centrifuge" and call["action"] in ("openLid", "closeLid"):
            assert leases["/World/Lab/Centrifuge"] == "/World/Lab/Centrifuge"


def test_absent_and_explicitly_disabled_optional_layout_fields_keep_identical_recipe():
    layout, teaching, binding, geometry = recipe_inputs()
    baseline = compile_recipe(layout, teaching, binding, geometry,
                              owner="auto", epoch=0, round_id="unchanged")
    layout.update(transportWaypoints={}, centrifugeLidSelfCheck=False)
    assert compile_recipe(layout, teaching, binding, geometry,
                          owner="auto", epoch=0, round_id="unchanged") == baseline
