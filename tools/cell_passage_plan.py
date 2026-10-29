"""Compile the teaching passage into ordinary client-executed Operation Plans.

Planned stations and leases below select calls and expectations. They do not
update physical poses, material or successful results in the platform.
"""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from tools.cell_passage_recipe import STAGES

LAB = "/World/Lab"
WORKCELL, ROBOT, RAIL = LAB + "/LiquidHandler", LAB + "/Robot/fr3_link0", LAB + "/RailCarriage"
CENTRIFUGE = LAB + "/Centrifuge"
WELLS = ("A1", "A2", "A3", "B1", "B2", "B3")


class _Recipe:
    def __init__(self, layout, teaching, binding, geometry, owner, epoch, round_id):
        if (teaching["failed"] or not teaching["locations"]
                or any(not row["valid"] for row in teaching["locations"])):
            raise ValueError("Complete reach teaching is required; transport still needs qualification")
        self.layout, self.geometry = layout, geometry
        self.config = binding["config"]
        if layout.get("tipDiscardSite") and self.config["wasteSite"] != layout["tipDiscardSite"]:
            raise ValueError("Workcell disposal binding does not match the authored discard Site")
        self.vessels = {row["id"]: row for row in self.config["vessels"]}
        self.locations = teaching["locations"]
        self.transport_waypoints = layout.get("transportWaypoints", {})
        if not isinstance(self.transport_waypoints, dict):
            raise ValueError("transportWaypoints must map taught locations to joint-vector lists")
        location_names = {row["name"] for row in self.locations}
        for name, points in self.transport_waypoints.items():
            if name not in location_names:
                raise ValueError(f"Unknown transport waypoint location: {name}")
            if name in layout.get("incubatorTransitLocations", {}):
                raise ValueError(f"Transport waypoints overlap an incubator transit location: {name}")
            if not isinstance(points, list) or any(
                not isinstance(point, list) or len(point) != 7 or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    for value in point
                ) for point in points
            ):
                raise ValueError("Transport waypoints require lists of seven finite, nonboolean joint values")
        self.stations = dict(layout["initialStations"])
        self.leases = {}
        self.owner, self.epoch, self.round_id = owner, epoch, round_id
        self.tip_index, self.rail = 0, None
        self.q = [0.0, 0.0, 0.0]
        self.plans, self.steps = [], []
        self.phase = 0
        self.environment = all(name in layout for name in (
            "incubatorEntity", "cabinetEntity", "environmentLimits",
        ))
        if self.environment and not {layout["incubatorEntity"], layout["cabinetEntity"]}.issubset(
            self.config["managedDevices"]
        ):
            raise ValueError("Environment devices must participate in source-side managed control")

    def stage(self, number):
        if self.plans:
            self.cut()
        self.phase = number
        self.steps = []
        self.cuts = []
        self.plans.append({"stage": number, "cutAfterSteps": self.cuts, "document": {
            "title": f"Cell Passage {self.round_id} · {number}/7 · {STAGES[number - 1]['title']}",
            "target": "session", "steps": self.steps,
        }})

    def cut(self):
        self.cuts.append(len(self.steps))

    def do(self, entity, action, params=None, *, expect=None):
        identity = self.phase * 100000 + len(self.steps) + 1
        operation = f"cp-{self.round_id}-s{identity}"
        step = {"id": identity, "do": {"entity": entity, "action": action, "params": {
            **(params or {}), "owner": self.owner, "epoch": self.epoch, "operationId": operation,
        }}}
        if expect:
            step["expect"] = expect
        self.steps.append(step)
        return operation

    def check(self, entity, prop, op, value):
        self.steps.append({"id": self.phase * 100000 + len(self.steps) + 1,
                           "check": {"entity": entity, "property": prop, op: value}})

    def reserve(self, resource, device):
        if self.leases.get(resource) == device:
            return
        self.release(resource)
        self.do(WORKCELL, "reserve", {"resource": resource, "device": device})
        self.leases[resource] = device

    def release(self, resource):
        if resource in self.leases:
            self.do(WORKCELL, "release", {"resource": resource})
            del self.leases[resource]

    def location(self, site, body):
        candidates = [row for row in self.locations
                      if row["site"] == site and row.get("body") in (None, body)]
        candidates = [row for row in candidates if all(
            condition.get("property") != "station"
            or self.stations.get(condition["entity"]) == condition.get("equals")
            for condition in row["requires"]
        )]
        if len(candidates) != 1:
            raise ValueError(f"Missing or ambiguous taught location for {body} at {site}")
        return candidates[0]

    def rail_to(self, location):
        value = location["railPositionM"]
        if value != self.rail:
            self.do(ROBOT, "home")  # Retains a held payload; no implicit gripper release.
            self.do(RAIL, "move", {"position": value})
            self.rail = value

    def park(self):
        target = list(self.layout["pipetteCalibration"]["qUpperM"])
        self.do(WORKCELL, "move", {"position": target})
        self.q = target

    def clear_incubator(self):
        self.do(ROBOT, "home")
        self.do(RAIL, "move", {"position": 0.4})
        self.rail = 0.4

    def transport(self, body, target):
        self.park()
        source = self.stations[body]
        pickup, placement = self.location(source, body), self.location(target, body)
        for resource in (body, source, target):
            self.reserve(resource, ROBOT)
        transit = self.layout.get("incubatorTransitLocations", {})

        def enter_or_leave(location, *, leaving=False):
            name = transit.get(location["name"])
            if name:
                descriptor = next(row for row in self.locations if row["name"] == name)
                self.reserve(descriptor["site"], ROBOT)
                self.do(ROBOT, "moveTo", {"location": name, "pose": "approach"})
            points = self.transport_waypoints.get(location["name"], [])
            for positions in reversed(points) if leaving else points:
                self.do(ROBOT, "moveJoints", {"positions": positions})

        self.rail_to(pickup)
        enter_or_leave(pickup)
        self.do(ROBOT, "pick", {"location": pickup["name"]},
                expect={"entity": ROBOT, "property": "holding", "equals": body})
        enter_or_leave(pickup, leaving=True)
        self.rail_to(placement)
        enter_or_leave(placement)
        self.do(ROBOT, "place", {"location": placement["name"]},
                expect={"entity": ROBOT, "property": "holding", "isNull": True})
        enter_or_leave(placement, leaving=True)
        self.check(body, "station", "equals", target)
        self.check(body, "settled", "equals", True)
        self.stations[body] = target
        for resource in (source, target, body):
            self.release(resource)
        for location in (pickup, placement):
            if location["name"] in transit:
                name = transit[location["name"]]
                self.release(next(row["site"] for row in self.locations if row["name"] == name))
        self.cut()

    def lid(self, name, opened):
        body = self.layout["bodies"][name]
        lid = self.layout["lids"][body]
        self.reserve(body, ROBOT)
        destination = self.layout["lidParking"][lid] if opened else body + "/LidSeat"
        self.transport(lid, destination)
        self.check(body, "lidClosed", "equals", not opened)
        self.release(body)
        self.cut()

    def move_tip(self, world):
        calibration = self.layout["pipetteCalibration"]
        delta = [value - calibration["tipOriginM"][i] for i, value in enumerate(world)]
        axes = calibration["axisDirections"]
        if any(abs(sum(a * b for a, b in zip(left, right, strict=True)) - (i == j)) > 1e-8
               for i, left in enumerate(axes) for j, right in enumerate(axes)):
            raise ValueError("This recipe requires an orthonormal calibrated pipette frame")
        target = [sum(a * b for a, b in zip(axis, delta, strict=True)) for axis in axes]
        if any(not math.isfinite(value) or value < calibration["qLowerM"][i] - 1e-8
               or value > calibration["qUpperM"][i] + 1e-8 for i, value in enumerate(target)):
            raise ValueError(f"Pipette target exceeds scene limits: {world}")
        height = calibration["qUpperM"][2]
        for point in ([self.q[0], self.q[1], height], [target[0], target[1], height], target):
            self.do(WORKCELL, "move", {"position": list(point)})
        self.q = target

    def content_position(self, identity):
        row = self.vessels[identity]
        body = row["entityKey"]
        if body not in self.stations:
            return self.geometry["staticSites"][row["site"]]
        station = self.stations[body]
        if row.get("requiredStation") != station:
            raise ValueError(f"Planned liquid access is not at its required station: {identity}")
        offset = self.geometry["localContents"][identity]
        yaw = self.layout.get("stationYawRad", {}).get(station, 0)
        rotated = [math.cos(yaw) * offset[0] - math.sin(yaw) * offset[1],
                   math.sin(yaw) * offset[0] + math.cos(yaw) * offset[1], offset[2]]
        return [value + rotated[i] for i, value in enumerate(self.layout["stations"][station])]

    def fresh_tip(self):
        if self.tip_index >= len(self.layout["tips"]):
            raise ValueError("Recipe exceeds the fixed initial tip inventory")
        tip = self.layout["tips"][self.tip_index]
        self.tip_index += 1
        self.move_tip(self.geometry["staticSites"][tip["site"]])
        self.do(WORKCELL, "pickupTip", {"tip": tip["id"]})

    def discard_tip(self):
        self.reserve(self.vessels["SolidWaste"]["entityKey"], WORKCELL)
        site = self.layout.get("tipDiscardSite")
        self.move_tip(self.geometry["staticSites"][site] if site else self.content_position("SolidWaste"))
        self.do(WORKCELL, "discardTip", {"waste": "SolidWaste"})

    def transfer(self, source, target, amount=None, *, action="aspirate", **quantity):
        for identity in (source, target):
            self.reserve(self.vessels[identity]["entityKey"], WORKCELL)
        self.fresh_tip()
        self.move_tip(self.content_position(source))
        self.do(WORKCELL, action, {"vessel": source, **quantity,
                                   **({"volumeUl": amount} if amount is not None else {})})
        self.move_tip(self.content_position(target))
        self.do(WORKCELL, "dispenseAll", {"vessel": target})
        self.discard_tip()
        self.cut()

    def mix(self, identity):
        self.reserve(self.vessels[identity]["entityKey"], WORKCELL)
        self.fresh_tip()
        self.move_tip(self.content_position(identity))
        self.do(WORKCELL, "mix", {"vessel": identity, "volumeUl": 900})
        self.discard_tip()
        self.cut()

    def observe(self, plate, kind):
        body = self.layout["bodies"][plate]
        self.reserve(body, WORKCELL)
        for well in WELLS:
            self.do(WORKCELL, kind, {"vessel": f"{plate}/{well}"},
                    expect={"entity": WORKCELL, "property": "lastObservedLive", "gt": 0})

    def batch(self, samples, load):
        self.do(CENTRIFUGE, "openLid")
        for name, slot in load:
            self.transport(self.layout["bodies"][name], self.layout["slotSites"][slot - 1])
            self.reserve(self.layout["bodies"][name], CENTRIFUGE)
        self.do(ROBOT, "home")
        self.do(RAIL, "move", {"position": 0.4})
        self.rail = 0.4
        self.do(CENTRIFUGE, "closeLid")
        cycle = self.do(CENTRIFUGE, "start", {
            "rpm": 800, "durationSeconds": self.config["centrifugeSeconds"],
        })
        self.check(CENTRIFUGE, "completedCycle", "equals", cycle)
        self.check(CENTRIFUGE, "actualRotorTurns", "gt", 0)
        self.do(WORKCELL, "pellet", {"centrifuge": CENTRIFUGE, "cycle": cycle, "vessels": samples})
        self.do(CENTRIFUGE, "openLid")
        for name, _ in load:
            index = int(name[1:]) if name.startswith("S") else None
            target = LAB + (f"/Sites/FlexTube{index}" if index else "/Sites/Rack" + name)
            self.transport(self.layout["bodies"][name], target)


def compile_recipe(layout, teaching, binding, geometry, *, owner, epoch, round_id, max_steps=128):
    recipe = _Recipe(layout, teaching, binding, geometry, owner, epoch, round_id)
    body = layout["bodies"]
    recipe.stage(1)
    if recipe.environment:
        recipe.check(layout["cabinetEntity"], "cabinetReady", "equals", True)
    for entity in (RAIL, CENTRIFUGE, LAB + "/Incubator/OuterDoor", LAB + "/Incubator/InnerDoor"):
        recipe.reserve(entity, entity)
    recipe.do(LAB + "/Incubator/OuterDoor", "open")
    recipe.do(LAB + "/Incubator/InnerDoor", "open")
    recipe.transport(body["Mother"], LAB + "/Sites/Imager")
    recipe.observe("Mother", "observe")
    recipe.clear_incubator()
    recipe.do(LAB + "/Incubator/InnerDoor", "close")
    recipe.do(LAB + "/Incubator/OuterDoor", "close")
    if layout.get("centrifugeLidSelfCheck") is True:
        recipe.do(CENTRIFUGE, "openLid")
        recipe.do(CENTRIFUGE, "closeLid")

    recipe.stage(2)
    if recipe.environment:
        recipe.check(layout["cabinetEntity"], "cabinetReady", "equals", True)
    recipe.transport(body["Mother"], LAB + "/Sites/FlexPlate")
    recipe.lid("Mother", True)
    for index in range(1, 7):
        recipe.transport(body[f"S{index}"], LAB + f"/Sites/FlexTube{index}")
        recipe.lid(f"S{index}", True)
    for index, well in enumerate(WELLS, 1):
        mother = f"Mother/{well}"
        recipe.transfer(mother, "WasteLiquid", action="aspirateAvailable")
        recipe.transfer("ReservoirPBS", mother, 200)
        recipe.transfer(mother, "WasteLiquid", action="aspirateAvailable")
        recipe.transfer("ReservoirTrypsin", mother, 200)
        recipe.do(WORKCELL, "wait", {"seconds": recipe.config["detachSeconds"]})
        recipe.move_tip(recipe.content_position(mother))
        recipe.do(WORKCELL, "detach", {"vessel": mother})
        recipe.transfer("ReservoirMedium", mother, action="aspirateTopUp",
                        targetVessel=mother, targetVolumeUl=1000)
        recipe.transfer(mother, f"S{index}", action="aspirateAvailable")
    recipe.lid("Mother", False)
    recipe.transport(body["Mother"], LAB + "/Sites/WasteMother")
    for index in range(1, 7):
        recipe.lid(f"S{index}", False)

    recipe.stage(3)
    recipe.batch(["S1", "S2", "S3", "S4"], [("S1", 1), ("S2", 3), ("S3", 2), ("S4", 4)])
    recipe.stage(4)
    recipe.batch(["S5", "S6"], [("S5", 1), ("B5", 3), ("S6", 2), ("B6", 4)])

    recipe.stage(5)
    if recipe.environment:
        recipe.check(layout["cabinetEntity"], "cabinetReady", "equals", True)
    recipe.transport(body["Counting"], LAB + "/Sites/FlexPlate")
    recipe.lid("Counting", True)
    for index, well in enumerate(WELLS, 1):
        sample = f"S{index}"
        recipe.lid(sample, True)
        recipe.transfer(sample, "WasteLiquid", action="aspirateAvailable", reserveUl=50)
        recipe.transfer("ReservoirMedium", sample, action="aspirateTopUp",
                        targetVessel=sample, targetVolumeUl=1000)
        recipe.mix(sample)
        recipe.transfer(sample, f"Counting/{well}", 20)
    recipe.lid("Counting", False)
    recipe.transport(body["Counting"], LAB + "/Sites/Imager")
    recipe.observe("Counting", "count")
    recipe.transport(body["Counting"], LAB + "/Sites/WasteCounting")

    recipe.stage(6)
    if recipe.environment:
        recipe.check(layout["cabinetEntity"], "cabinetReady", "equals", True)
    for plate in ("DaughterA", "DaughterB"):
        recipe.transport(body[plate], LAB + "/Sites/FlexPlate")
        recipe.lid(plate, True)
        for index, well in enumerate(WELLS, 1):
            target = f"{plate}/{well}"
            if plate == "DaughterA":
                recipe.transfer(f"S{index}", target, action="aspirateFraction", fraction=0.5)
            else:
                recipe.transfer(f"S{index}", target, action="aspirateAvailable")
            recipe.transfer("ReservoirMedium", target, action="aspirateTopUp",
                            targetVessel=target, targetVolumeUl=1000)
        recipe.lid(plate, False)
        recipe.transport(body[plate], LAB + f"/Sites/{plate}Ready")
    for index in range(1, 7):
        recipe.lid(f"S{index}", False)
        recipe.transport(body[f"S{index}"], LAB + f"/Sites/RackS{index}")

    recipe.stage(7)
    recipe.do(LAB + "/Incubator/OuterDoor", "open")
    recipe.do(LAB + "/Incubator/InnerDoor", "open")
    recipe.transport(body["DaughterA"], LAB + "/Sites/IncubatorA")
    recipe.transport(body["DaughterB"], LAB + "/Sites/IncubatorB")
    recipe.clear_incubator()
    recipe.do(LAB + "/Incubator/InnerDoor", "close")
    recipe.do(LAB + "/Incubator/OuterDoor", "close")
    recipe.do(CENTRIFUGE, "closeLid")
    if recipe.environment:
        incubator, cabinet = layout["incubatorEntity"], layout["cabinetEntity"]
        recipe.reserve(incubator, incubator)
        recipe.do(incubator, "waitReady", {"timeoutSeconds": 90})
        for entity, prop, value in (
            (incubator, "environmentReady", True), (incubator, "doorKnown", True),
            (incubator, "doorOpen", False), (incubator, "signalSource", "modeled"),
            (cabinet, "cabinetReady", True), (cabinet, "signalSource", "modeled"),
            (cabinet, "blowerOn", True), (cabinet, "uvOn", False),
        ):
            recipe.check(entity, prop, "equals", value)
        for prop, (lower, upper) in layout["environmentLimits"].items():
            entity = cabinet if prop in ("inflowMs", "sashHeightCm") else incubator
            recipe.check(entity, prop, "gte", lower)
            recipe.check(entity, prop, "lte", upper)
        recipe.check(incubator, "setpointC", "gte", 36.9)
        recipe.check(incubator, "setpointC", "lte", 37.1)
    recipe.park()
    for resource in list(recipe.leases):
        recipe.release(resource)
    for name in ("DaughterA", "DaughterB", "Mother", "Counting", *[f"S{i}" for i in range(1, 7)], "B5", "B6"):
        recipe.check(body[name], "station", "equals", recipe.stations[body[name]])
        recipe.check(body[name], "settled", "equals", True)
        recipe.check(body[name], "lidClosed", "equals", True)
    for entity, prop, value in ((WORKCELL, "auditPassed", True), (WORKCELL, "samplesIndependent", True),
                                (WORKCELL, "inFlightUl", 0), (ROBOT, "controlSafe", True)):
        recipe.check(entity, prop, "equals", value)
    recipe.cut()
    result = {"schema": "lw.cell-passage-recipe/1", "roundId": round_id,
            "mechanicallyQualified": False, "environmentChecksAvailable": recipe.environment,
            "plans": recipe.plans, "tipsRequired": recipe.tip_index,
            "teachingConditions": {"daughterVolumeUl": 1000, "sampleVolumeUl": 20,
                                   "collectionTargetUl": 1000, "resuspensionTargetUl": 1000,
                                   "mixStrokeUl": 900,
                                   "requestedCentrifugeRpm": 800, "physicalRpmCap": 60,
                                   "finalVolumeToleranceUl": 0.01, "cellConservationTolerance": 0.000001,
                                   "siblingLiveTolerance": 1}}
    return paginate_recipe(result, max_steps=max_steps) if max_steps else result


def paginate_recipe(recipe, *, max_steps=128):
    """Split only at completed step boundaries, preferring complete logical groups.

    Step/action identities and flattened content remain unchanged; a page boundary
    never resets material, releases a payload or creates a successful result.
    """
    if not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    plans = []
    for stage in recipe["plans"]:
        steps, pages, start = stage["document"]["steps"], [], 0
        while start < len(steps):
            limit = min(start + max_steps, len(steps))
            cuts = [position for position in stage["cutAfterSteps"] if start < position <= limit]
            end = max(cuts) if cuts else limit
            pages.append(steps[start:end])
            start = end
        for part, page in enumerate(pages, 1):
            plans.append({"stage": stage["stage"], "part": part, "parts": len(pages), "document": {
                "title": stage["document"]["title"] + f" · part {part}/{len(pages)}",
                "target": "session", "steps": page,
            }})
    return {**recipe, "plans": plans, "maxPartSteps": max_steps}


def load_inputs(library):
    """Read generated package geometry without importing a solver or mutating the package."""
    from pxr import Sdf, Usd, UsdGeom

    library = Path(library).resolve()
    scene = library / "scenes/cell-passage/1"
    layout = json.loads((scene / "mechanical-layout.json").read_text(encoding="utf8"))
    teaching = json.loads((scene / "teaching.json").read_text(encoding="utf8"))
    bindings = yaml.safe_load((scene / "devices.yaml").read_text(encoding="utf8"))["entities"]
    binding = next(row for row in bindings if row["type"] == "cell-passage-workcell/v1")
    text = (scene / "stage.usda").read_text(encoding="utf8")
    text = re.sub(r"@library:assets/([^@]+)@",
                  lambda match: "@" + (library / "assets" / match[1]).as_posix() + "@", text)
    layer = Sdf.Layer.CreateAnonymous("passage-recipe-readonly.usda")
    if not layer.ImportFromString(text):
        raise ValueError("Cannot read the generated scene stage")
    stage, cache = Usd.Stage.Open(layer), UsdGeom.XformCache()
    geometry = {"localContents": {}, "staticSites": {}}

    def matrix(key):
        prim = stage.GetPrimAtPath(key)
        if not prim.IsValid():
            raise ValueError("Missing actual scene Site/body: " + key)
        return cache.GetLocalToWorldTransform(prim)

    for row in binding["config"]["vessels"]:
        world = matrix(row["site"]).ExtractTranslation()
        local = matrix(row["entityKey"]).GetInverse().Transform(world)
        geometry["localContents"][row["id"]] = list(local)
        if row["entityKey"] not in layout["initialStations"]:
            geometry["staticSites"][row["site"]] = list(world)
    for tip in layout["tips"]:
        geometry["staticSites"][tip["site"]] = list(matrix(tip["site"]).ExtractTranslation())
    if layout.get("tipDiscardSite"):
        geometry["staticSites"][layout["tipDiscardSite"]] = list(
            matrix(layout["tipDiscardSite"]).ExtractTranslation()
        )
    return layout, teaching, binding, geometry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--owner", default="auto")
    parser.add_argument("--epoch", type=int, default=0)
    parser.add_argument("--round", required=True)
    args = parser.parse_args()
    recipe = compile_recipe(
        *load_inputs(args.library), owner=args.owner, epoch=args.epoch, round_id=args.round
    )
    args.out.mkdir(parents=True, exist_ok=False)
    sizes = []
    for plan in recipe["plans"]:
        raw = json.dumps(plan["document"], ensure_ascii=False, separators=(",", ":")).encode("utf8")
        path = args.out / f"stage-{plan['stage']}-part-{plan['part']}.plan.json"
        path.write_bytes(raw)
        sizes.append({"stage": plan["stage"], "part": plan["part"], "parts": plan["parts"],
                      "steps": len(plan["document"]["steps"]),
                      "jsonBytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    recipe["documents"] = sizes
    (args.out / "recipe.json").write_text(
        json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    print(json.dumps({"output": str(args.out), "stages": sizes,
                      "tipsRequired": recipe["tipsRequired"], "mechanicallyQualified": False}))


if __name__ == "__main__":
    main()
