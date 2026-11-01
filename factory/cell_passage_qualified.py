"""Pure demo Config overlay from composed USD families; no geometry or file writes."""

from copy import deepcopy


def qualified_placement_config(stage, layout, bindings, *, enabled=False):
    if not enabled:
        return {}
    from pxr import UsdGeom, UsdPhysics

    motion = next(row for row in bindings if row["program"] == "labware.motion")
    config = motion["config"]
    movable = set(config["tubes"])
    lab = next(iter(layout["bodies"].values())).split("/Labware/")[0]
    colliders = {
        str(p.GetPath()): p
        for p in stage.Traverse()
        if p.HasAPI(UsdPhysics.CollisionAPI)
        and UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() is not False
    }

    def beneath(root):
        return {key for key in colliders if key == root or key.startswith(root + "/")}

    def shapes(body):
        return beneath(body)

    board_names = ("Mother", "DaughterA", "DaughterB", "Counting")
    board_bodies = {layout["bodies"][name] for name in board_names}
    tube_bodies = {layout["bodies"][name] for name in ("S1", "S2", "S3", "S4", "S5", "S6", "B5", "B6")}
    board_lids = {layout["lids"][key] for key in board_bodies}
    tube_lids = {layout["lids"][key] for key in tube_bodies}
    if movable != board_bodies | tube_bodies | board_lids | tube_lids:
        raise ValueError("Qualified demo families must cover exactly the configured movable labware")
    board_routes = {
        "Mother": ["IncubatorMother", "Imager", "FlexPlate", "WasteMother"],
        "DaughterA": ["DaughterAReady", "FlexPlate", "IncubatorA"],
        "DaughterB": ["DaughterBReady", "FlexPlate", "IncubatorB"],
        "Counting": ["CountingReady", "FlexPlate", "Imager", "WasteCounting"],
    }
    stations = {}
    for body in movable:
        if body in board_bodies:
            name = next(name for name in board_names if layout["bodies"][name] == body)
            stations[body] = [lab + "/Sites/" + station for station in board_routes[name]]
        elif body in tube_bodies:
            name = next(name for name, key in layout["bodies"].items() if key == body)
            flex = int(name[1:]) if name.startswith("S") else 7 if name == "B5" else 8
            stations[body] = [
                lab + "/Sites/Rack" + name,
                lab + f"/Sites/FlexTube{flex}",
                *layout["slotSites"],
            ]
        else:
            owner = next(key for key, lid in layout["lids"].items() if lid == body)
            stations[body] = [owner + "/LidSeat", layout["lidParking"][body]]
    pairs = set()

    def pair(left, right):
        for a in left:
            for b in right:
                if a != b:
                    pairs.add(tuple(sorted((a, b))))

    # Finger candidates are exact USD shapes. Runtime/L0 must activate them
    # against the current grasp candidate/actual held assembly, not this full table.
    finger_bodies = [key.rsplit("/", 1)[0] for key in config["fingerPadShapes"]]
    finger_shapes = set().union(*(beneath(key) for key in finger_bodies))
    for body in movable:
        pair(shapes(body), finger_shapes)
    for owner, lid in layout["lids"].items():
        pair(shapes(owner), shapes(lid))
    cache = UsdGeom.BBoxCache(0, ["default", "render", "proxy"])

    def supports(site):
        name = site.rsplit("/", 1)[-1]
        if name.startswith("Rack"):
            base = lab + "/Labware/CollectionRack/Frame/" + name[4:]
            return (
                beneath(base + "Floor")
                | beneath(base + "Guide")
                | beneath(base + "Index0")
                | beneath(base + "Index2")
            )
        if site in layout["slotSites"]:
            base = site
            return (
                beneath(base + "Floor")
                | beneath(base + "Guide")
                | beneath(base + "Index0")
                | beneath(base + "Index2")
            )
        fixture = lab + "/Fixtures/" + name
        direct = beneath(fixture + "/Support") | beneath(fixture + "/Guide")
        if direct:
            return direct
        # Retained device support surfaces: derive exact candidates at the
        # authored station's support plane. Doors/robot/other vessels are excluded.
        if name == "Imager":
            root = lab + "/Imager/"
        elif name.startswith("Incubator"):
            root = lab + "/Incubator/"
        else:
            return set()
        prim = stage.GetPrimAtPath(site)
        if not prim:
            raise ValueError("Missing placement Site: " + site)
        p = UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()
        result = set()
        for key, shape in colliders.items():
            if not key.startswith(root) or "Door" in key:
                continue
            bounds = cache.ComputeWorldBound(shape).ComputeAlignedRange()
            low, high = bounds.GetMin(), bounds.GetMax()
            if (
                abs(high[2] - p[2]) <= 0.020
                and low[2] <= p[2] + 0.005
                and low[0] <= p[0] + 0.0675
                and high[0] >= p[0] - 0.0675
                and low[1] <= p[1] + 0.046
                and high[1] >= p[1] - 0.046
            ):
                result.add(key)
        return result

    for body, sites in stations.items():
        grip = config["gripShapePrefixes"][body]
        support_shapes = {
            k for k in shapes(body) if not (k == grip or k.startswith(grip + "/")) and "/CarrierArm" not in k
        }
        for site in sites:
            if site.endswith("/LidSeat"):
                continue  # Own-object assembly pairs were generated above.
            pair(support_shapes, supports(site))
    expected = [list(value) for value in sorted(pairs)]
    overlay = {
        motion["key"]: {
            "qualifiedPlacement": True,
            "placementStations": deepcopy(stations),
            "initialPlacements": {
                key: value for key, value in layout["initialStations"].items() if key in movable
            },
            "expectedContactPairs": expected,
            "seatingContactPairs": [],
        }
    }
    for binding in bindings:
        if binding["program"] in ("cell_passage.joint", "cell_passage.centrifuge"):
            overlay[binding["key"]] = {
                "qualifiedPlacement": True,
                "expectedContactPairs": expected,
                "fingerBodyKeys": finger_bodies,
                "contactAllowanceM": 0.002,
            }
            if binding["program"] == "cell_passage.joint":
                overlay[binding["key"]]["seatingContactPairs"] = []
    return overlay
