"""Fixed physical layout shared by the factory and offline qualification tools."""

from pxr import Gf, Sdf, UsdGeom, UsdPhysics

from factory.cell_passage import HANDLE, LAB, box, cylinder, rigid, ring, site, translate


def relocate(stage, root, fixed_joint, delta):
    translate(stage.GetPrimAtPath(root), delta)
    joint = UsdPhysics.FixedJoint(stage.GetPrimAtPath(fixed_joint))
    point = joint.GetLocalPos0Attr().Get()
    joint.GetLocalPos0Attr().Set(Gf.Vec3f(*(point[i] + delta[i] for i in range(3))))


def reference(stage, path, name, position):
    root = UsdGeom.Xform.Define(stage, path)
    root.GetPrim().GetReferences().AddReference(f"library:assets/labware/{name}/1/asset.usda", "/Asset")
    translate(root, position)
    return root


def centrifuge(stage, root=LAB + "/Centrifuge", origin=(-0.53, -0.65, 0.96)):
    """Four physical cup bores and the existing velocity-driven rotor contract."""
    housing = rigid(stage, root + "/Housing", 8.0)
    translate(housing, origin)
    cylinder(stage, root + "/Housing/Floor", (0, 0, -0.015), 0.19, 0.030)
    ring(stage, root + "/Housing/Wall", (0, 0, 0.047), 0.188, 0.094, 0.006, 32)
    fixed = UsdPhysics.FixedJoint.Define(stage, root + "/Mount")
    fixed.CreateBody1Rel().SetTargets([housing.GetPath()])
    fixed.CreateLocalPos0Attr(Gf.Vec3f(*origin))
    UsdPhysics.ArticulationRootAPI.Apply(fixed.GetPrim())
    rotor = rigid(stage, root + "/Rotor", 2.0)
    translate(rotor, (origin[0], origin[1], origin[2] + 0.035))
    cylinder(stage, root + "/Rotor/Disc", (0, 0, -0.004), 0.17, 0.008)
    slot_keys = []
    for number, (x, y) in enumerate(((0.12, 0), (0, 0.12), (-0.12, 0), (0, -0.12)), 1):
        cylinder(stage, root + f"/Rotor/Slot{number}Floor", (x, y, 0.002), 0.011, 0.004)
        ring(stage, root + f"/Rotor/Slot{number}Guide", (x, y, 0.014), 0.010, 0.020, 0.001, 24)
        key = root + f"/Rotor/Slot{number}"
        site(stage, key, (x, y, 0.004))
        slot_keys.append(key)
    spindle = UsdPhysics.RevoluteJoint.Define(stage, root + "/Spindle")
    spindle.CreateBody0Rel().SetTargets([housing.GetPath()])
    spindle.CreateBody1Rel().SetTargets([rotor.GetPath()])
    spindle.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 0.035))
    spindle.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
    spindle.CreateAxisAttr("Z")
    drive = UsdPhysics.DriveAPI.Apply(spindle.GetPrim(), "angular")
    drive.CreateTypeAttr("force")
    drive.CreateStiffnessAttr(0)
    drive.CreateDampingAttr(500)
    drive.CreateMaxForceAttr(500)
    drive.CreateTargetVelocityAttr(0)
    lid = rigid(stage, root + "/Lid", 1.0)
    translate(lid, (origin[0], origin[1] - 0.195, origin[2] + 0.145))
    box(stage, root + "/Lid/Cover", (0, 0.195, 0), (0.40, 0.40, 0.008), (0.32, 0.4, 0.47))
    ring(stage, root + "/Lid/Skirt", (0, 0.195, -0.025), 0.200, 0.05, 0.004, 32)
    hinge = UsdPhysics.RevoluteJoint.Define(stage, root + "/LidHinge")
    hinge.CreateBody0Rel().SetTargets([housing.GetPath()])
    hinge.CreateBody1Rel().SetTargets([lid.GetPath()])
    hinge.CreateLocalPos0Attr(Gf.Vec3f(0, -0.195, 0.145))
    hinge.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
    hinge.CreateAxisAttr("X")
    hinge.CreateLowerLimitAttr(0)
    hinge.CreateUpperLimitAttr(80.214)
    drive = UsdPhysics.DriveAPI.Apply(hinge.GetPrim(), "angular")
    drive.CreateTypeAttr("force")
    drive.CreateStiffnessAttr(1000)
    drive.CreateDampingAttr(50)
    drive.CreateMaxForceAttr(100)
    drive.CreateTargetPositionAttr(0)
    return slot_keys


def populate(stage):
    relocate(stage, LAB + "/Incubator", LAB + "/Joints/IncubatorFixed", (0.60, -0.23, 0))
    relocate(stage, LAB + "/LiquidHandler", LAB + "/Joints/LiquidHandlerFixed", (-0.40, -0.18, 0))
    # Removable upper shelves are omitted in this derived configuration.
    # The three plate bays share the lowest shelf; the unchanged hand and
    # carrier need more than the original 145 mm bay spacing.
    base = stage.GetPrimAtPath(LAB + "/Incubator/Base")
    base.GetReferences().SetReferences(
        [Sdf.Reference("library:assets/device/passage-incubator-single-level/1/asset.usda", "/Asset")]
    )
    for child in list(base.GetChildren()):
        if any(token in child.GetName() for token in ("shelf_2_", "shelf_3_", "shelf_4_")):
            stage.RemovePrim(child.GetPath())
    UsdPhysics.PrismaticJoint(stage.GetPrimAtPath(LAB + "/Joints/FlexZ")).GetUpperLimitAttr().Set(0.08)
    UsdPhysics.PrismaticJoint(stage.GetPrimAtPath(LAB + "/Joints/FlexX")).GetUpperLimitAttr().Set(0.12)
    for name in ("chemlab_corning_96well", "chemlab_flex_tiprack1000", "chemlab_nest_reservoir"):
        stage.RemovePrim("/World/Dressing/" + name)

    stations = {
        "IncubatorMother": (-0.925, 0.23, 1.0858),
        "IncubatorA": (-0.575, 0.23, 1.0858),
        "IncubatorB": (-0.750, 0.23, 1.0858),
        "Imager": (-0.05, -0.68, 1.232),
        "FlexPlate": (0.59, 0.29, 1.032),
        "DaughterAReady": (0.15, -0.66, 1.232),
        "DaughterBReady": (0.35, -0.66, 1.232),
        "CountingReady": (0.55, -0.66, 1.232),
        "WasteCounting": (0.60, -0.84, 1.232),
        "WasteMother": (0.40, -0.50, 1.232),
    }
    for i, name in enumerate([f"S{i}" for i in range(1, 7)] + ["B5", "B6"]):
        stations["Rack" + name] = (-0.30 + (i % 4) * 0.055, -0.58 + (i // 4) * 0.055, 1.032)
        stations[f"FlexTube{i + 1}"] = (0.565 + (i % 4) * 0.045, 0.405 + (i // 4) * 0.045, 1.032)
    for i in range(4):
        stations[f"Lid{i + 1}"] = (-0.2 + i * 0.2, -0.85, 1.232)
    for i in range(8):
        stations[f"Lid{i + 5}"] = (-0.3 + i * 0.1, -0.44, 1.132)
    for name, point in stations.items():
        value = site(stage, LAB + "/Sites/" + name, point)
        if name == "IncubatorA":
            value.AddRotateZOp().Set(180.0)
        if not name.startswith("Incubator"):
            size = (
                (0.135, 0.092, 0.016) if not name.startswith(("Rack", "FlexTube")) else (0.025, 0.025, 0.012)
            )
            surface = point[2] - (0.012 if name in ("Lid1", "Lid2", "Lid3", "Lid4") else 0)
            box(
                stage,
                LAB + "/Fixtures/" + name + "/Support",
                (point[0], point[1], surface - size[2] / 2),
                size,
            )
            if name.startswith(("Rack", "FlexTube")):
                ring(
                    stage,
                    LAB + "/Fixtures/" + name + "/Guide",
                    (point[0], point[1], point[2] + 0.006),
                    0.010,
                    0.012,
                    0.001,
                    16,
                )

    bodies, lids, grips = {}, {}, {}
    starts = {
        "Mother": "IncubatorMother",
        "DaughterA": "DaughterAReady",
        "DaughterB": "DaughterBReady",
        "Counting": "CountingReady",
    }
    for name, start in starts.items():
        point = stations[start]
        key = LAB + "/Labware/" + name
        reference(stage, key, "passage-six-well-carrier", point)
        lid = key + "Lid"
        reference(stage, lid, "passage-plate-lid", (point[0], point[1], point[2] + 0.018))
        bodies[name], lids[key] = key, lid
        grips[key], grips[lid] = list(HANDLE), [-HANDLE[0], 0, HANDLE[2]]
    for name in [f"S{i}" for i in range(1, 7)] + ["B5", "B6"]:
        point = stations["Rack" + name]
        key = LAB + "/Labware/" + name
        reference(stage, key, "passage-collection-tube", point)
        lid = key + "Lid"
        reference(stage, lid, "passage-tube-cap", (point[0], point[1], point[2] + 0.05))
        bodies[name], lids[key] = key, lid
        grips[key], grips[lid] = [0.035, 0, 0.038], [-0.035, 0, 0.022]

    for name, point in {
        "ReservoirPBS": (0.49, 0.49, 1.031),
        "ReservoirTrypsin": (0.49, 0.57, 1.031),
        "ReservoirMedium": (0.59, 0.565, 1.031),
        "WasteLiquid": (0.79, 0.405, 1.031),
        "SolidWaste": (0.79, 0.25, 1.031),
    }.items():
        key = LAB + "/Labware/" + name
        reference(stage, key, "passage-reservoir" if name.startswith("Reservoir") else "passage-waste", point)
        bodies[name] = key
        box(stage, LAB + "/Fixtures/" + name, (point[0], point[1], point[2] - 0.0355), (0.09, 0.07, 0.071))

    # Individual tips have real hollow shells, fixed rack bores, and a bottom
    # Site. The attached tip endpoint moves with the original CarriageZ body.
    tips = []
    for row in range(8):
        for col in range(12):
            name = f"Tip{row * 12 + col + 1:03d}"
            point = (0.6905 + col * 0.009, 0.5085 + row * 0.009, 1.034)
            key = LAB + "/Labware/" + name
            reference(stage, key, "passage-tip-1000", point)
            site(stage, LAB + "/Labware/TipRack/" + name, point)
            cylinder(
                stage,
                LAB + "/Fixtures/TipRack/" + name + "Support",
                (point[0], point[1], point[2] - 0.001),
                0.003,
                0.002,
            )
            ring(
                stage,
                LAB + "/Fixtures/TipRack/" + name + "Guide",
                (point[0], point[1], point[2] + 0.014),
                0.0022,
                0.010,
                0.0005,
                12,
            )
            tips.append({"id": name, "bodyKey": key, "site": LAB + "/Labware/TipRack/" + name})
    tip_site = LAB + "/LiquidHandler/CarriageZ/TipSite"
    cache = UsdGeom.XformCache()
    carriage = stage.GetPrimAtPath(LAB + "/LiquidHandler/CarriageZ")
    local = cache.GetLocalToWorldTransform(carriage).GetInverse().Transform(Gf.Vec3d(0.7, 0.4, 1.034))
    site(stage, tip_site, local)
    pump = rigid(stage, LAB + "/LiquidHandler/PumpPiston", 0.025)
    parent = pump.GetPrim().GetParent()
    local_pump = cache.GetLocalToWorldTransform(parent).GetInverse().Transform(Gf.Vec3d(0.72, 0.4, 1.25))
    translate(pump, local_pump)
    cylinder(stage, pump.GetPath().pathString + "/Piston", (0, 0, 0), 0.006, 0.018)
    joint = UsdPhysics.PrismaticJoint.Define(stage, LAB + "/Joints/FlexPump")
    joint.CreateBody0Rel().SetTargets([carriage.GetPath()])
    joint.CreateBody1Rel().SetTargets([pump.GetPath()])
    anchor = cache.GetLocalToWorldTransform(carriage).GetInverse().Transform(Gf.Vec3d(0.72, 0.4, 1.25))
    joint.CreateLocalPos0Attr(Gf.Vec3f(*anchor))
    joint.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
    joint.CreateAxisAttr("Z")
    joint.CreateLowerLimitAttr(0)
    joint.CreateUpperLimitAttr(0.01)
    joint.CreateCollisionEnabledAttr(False)  # Parent piston guide interface only.
    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear")
    drive.CreateTypeAttr("force")
    drive.CreateStiffnessAttr(20000)
    drive.CreateDampingAttr(100)
    drive.CreateMaxForceAttr(100)
    drive.CreateTargetPositionAttr(0)
    root = LAB + "/Centrifuge"
    value = UsdGeom.Xform.Define(stage, root)
    value.GetPrim().GetReferences().AddReference(
        "library:assets/device/passage-centrifuge/1/asset.usda", "/Asset"
    )
    translate(value, (-0.53, -0.65, 0.96))
    slot_keys = [root + f"/Rotor/Slot{i}" for i in range(1, 5)]
    return {
        "schema": "lw.cell-passage-layout/1",
        "mechanicallyQualified": False,
        "stations": {LAB + "/Sites/" + key: list(value) for key, value in stations.items()},
        "bodies": bodies,
        "lids": lids,
        "graspOffsets": grips,
        "tips": tips,
        "axisJoints": [LAB + "/Joints/Flex" + name for name in "XYZ"],
        "pumpJoint": LAB + "/Joints/FlexPump",
        "pumpStrokeM": 0.01,
        "tipSite": tip_site,
        "pipetteBody": LAB + "/LiquidHandler/CarriageZ",
        "slotSites": slot_keys,
        "stationYawRad": {LAB + "/Sites/IncubatorA": 3.141592653589793},
        "initialStations": {
            **{bodies[name]: LAB + "/Sites/" + station for name, station in starts.items()},
            **{
                bodies[name]: LAB + "/Sites/Rack" + name
                for name in [f"S{i}" for i in range(1, 7)] + ["B5", "B6"]
            },
            **{lid: body + "/LidSeat" for body, lid in lids.items()},
        },
        "lidParking": {lid: LAB + f"/Sites/Lid{i + 1}" for i, lid in enumerate(lids.values())},
        "pipetteCalibration": {
            "tipOriginM": [0.7, 0.4, 1.034],
            "axisDirections": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "qLowerM": [-0.24, -0.21, 0],
            "qUpperM": [0.12, 0.21, 0.08],
            "qualified": False,
        },
        "incubatorEntity": LAB + "/Incubator",
        "cabinetEntity": LAB + "/LiquidHandler/Base",
        "managedDevices": [
            LAB + "/Robot/fr3_link0",
            LAB + "/RailCarriage",
            LAB + "/Incubator/OuterDoor",
            LAB + "/Incubator/InnerDoor",
            LAB + "/Centrifuge",
            LAB + "/Incubator",
            LAB + "/LiquidHandler/Base",
        ],
        "environmentLimits": {
            "temperatureC": [36.5, 37.5],
            "co2Percent": [4.5, 5.5],
            "humidityPercent": [90, 100],
            "inflowMs": [0.4, 2.0],
            "sashHeightCm": [18, 22],
        },
        "environmentTargets": {"temperatureC": 37, "co2Percent": 5, "humidityPercent": 95},
    }
