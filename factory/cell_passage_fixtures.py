"""Self-authored physical fixtures; none claim to be the received OEM mechanisms."""

from pxr import Gf, UsdGeom, UsdPhysics

from factory.cell_passage import WELLS, box, cylinder, rigid, ring, site


def mounted(stage, root, mass):
    UsdGeom.Xform.Define(stage, root)
    body = rigid(stage, root + "/Frame", mass)
    joint = UsdPhysics.FixedJoint.Define(stage, root + "/Mount")
    joint.CreateBody1Rel().SetTargets([body.GetPath()])
    joint.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 0))
    UsdPhysics.ArticulationRootAPI.Apply(joint.GetPrim())
    return root + "/Frame"


def bench(stage, root, width=1.9, depth=0.6, height=0.86):
    frame = mounted(stage, root, 35)
    box(stage, frame + "/Top", (0, 0, height - 0.02), (width, depth, 0.04), (0.36, 0.40, 0.44))
    for x in (-width / 2 + 0.06, width / 2 - 0.06):
        for y in (-depth / 2 + 0.06, depth / 2 - 0.06):
            name = f"Leg{int(x > 0)}{int(y > 0)}"
            box(
                stage,
                frame + "/" + name,
                (x, y, (height - 0.04) / 2),
                (0.04, 0.04, height - 0.04),
                (0.21, 0.26, 0.31),
            )


def inverted_imager(stage, root):
    """An open six-objective receiver; images/counts are explicitly modeled elsewhere."""
    frame = mounted(stage, root, 3.0)
    box(stage, frame + "/Base", (0, 0, -0.140), (0.20, 0.16, 0.016), (0.2, 0.27, 0.34))
    for x in (-0.085, 0.085):
        for y in (-0.062, 0.062):
            name = f"Column{int(x > 0)}{int(y > 0)}"
            box(stage, frame + "/" + name, (x, y, -0.070), (0.012, 0.012, 0.124), (0.35, 0.4, 0.45))
            box(stage, frame + "/" + name + "Foot", (x, y, -0.260), (0.016, 0.016, 0.224), (0.3, 0.35, 0.4))
    for x in (-0.056, 0.056):
        box(stage, frame + f"/BridgeY{int(x > 0)}", (x, 0, -0.014), (0.012, 0.136, 0.012))
        for y in (-0.036, 0.036):
            box(
                stage,
                frame + f"/Seat{int(x > 0)}{int(y > 0)}",
                (x, y, -0.004),
                (0.012, 0.010, 0.008),
                (0.3, 0.6, 0.55),
            )
    for y in (-0.062, 0.062):
        box(stage, frame + f"/BridgeX{int(y > 0)}", (0, y, -0.014), (0.182, 0.012, 0.012))
    for x in (-0.0655, 0.0655):
        for y in (-0.030, 0.030):
            box(stage, frame + f"/LocatorX{int(x > 0)}{int(y > 0)}", (x, y, 0.0015), (0.002, 0.008, 0.003))
            box(
                stage,
                frame + f"/BracketX{int(x > 0)}{int(y > 0)}",
                ((1 if x > 0 else -1) * 0.0615, y, -0.004),
                (0.010, 0.008, 0.008),
            )
    for y in (-0.0444, 0.0444):
        box(stage, frame + f"/LocatorY{int(y > 0)}", (0, y, 0.0015), (0.010, 0.002, 0.003))
        box(
            stage,
            frame + f"/BracketY{int(y > 0)}",
            (0, (1 if y > 0 else -1) * 0.0535, -0.004),
            (0.010, 0.031, 0.008),
        )
    for well, (x, y, z) in WELLS.items():
        box(stage, frame + "/" + well + "CameraMount", (x, y, -0.126), (0.028, 0.025, 0.012))
        box(stage, frame + "/" + well + "Camera", (x, y, -0.100), (0.028, 0.025, 0.040), (0.18, 0.25, 0.32))
        cylinder(stage, frame + "/" + well + "Objective", (x, y, -0.066), 0.011, 0.028, (0.36, 0.42, 0.48))
        lens = cylinder(stage, frame + "/" + well + "Lens", (x, y, -0.042), 0.009, 0.020, (0.35, 0.62, 0.76))
        lens.CreateDisplayOpacityAttr([0.55])
        site(stage, frame + "/Focus" + well, (x, y, z))


def tube_rack(stage, root, *, pitch=0.055, deck_size=(0.22, 0.10)):
    frame = mounted(stage, root, 0.35)
    box(stage, frame + "/Base", (0, 0, 0.004), (*deck_size, 0.008))
    box(stage, frame + "/Deck", (0, 0, 0.154), (*deck_size, 0.012))
    for x in (-(deck_size[0] / 2 - .015), deck_size[0] / 2 - .015):
        for y in (-(deck_size[1] / 2 - .010), deck_size[1] / 2 - .010):
            box(stage, frame + f"/Post{int(x > 0)}{int(y > 0)}", (x, y, 0.084), (0.012, 0.012, 0.152))
    for index, name in enumerate([f"S{i}" for i in range(1, 7)] + ["B5", "B6"]):
        x, y = (index % 4 - 1.5) * pitch, (index // 4 - 0.5) * pitch
        box(stage, frame + "/" + name + "Floor", (x, y, 0.166), (0.025, 0.025, 0.012))
        ring(stage, frame + "/" + name + "Guide", (x, y, 0.178), 0.010, 0.012, 0.001, 16)
        for sign in (-1, 1):
            box(
                stage,
                frame + "/" + name + f"Index{sign + 1}",
                (x - 0.014, y + sign * 0.0045, 0.184),
                (0.008, 0.002, 0.024),
            )
        site(stage, frame + "/" + name, (x, y, 0.172))


def wide_tube_rack(stage, root):
    """Versioned demonstration fixture; the original 55 mm rack stays unchanged."""
    return tube_rack(stage, root, pitch=.065, deck_size=(.25, .12))
