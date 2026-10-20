"""Read physical grasp calibration from verified USD bytes, without Newton."""

import hashlib
import itertools
import json
import math
from fnmatch import fnmatchcase

from pxr import Gf, Usd, UsdGeom, UsdPhysics

from packages.library.posture import require_calibration, resolve_endpoint
from packages.library.templates import rebase, verify_snapshot
from packages.programs.robot_arm.grasp import validate_height


def _owner(prim):
    while prim:
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            return prim
        prim = prim.GetParent()
    raise ValueError("Physical grasp geometry has no rigid body owner")


def _pose(prim, owner):
    matrix, reset = UsdGeom.XformCache().ComputeRelativeTransform(prim, owner)
    if reset:
        raise ValueError("Physical grasp transform resets its rigid owner")
    _rigid(matrix)
    rotation = matrix.ExtractRotationQuat()
    return [*matrix.ExtractTranslation(), *rotation.GetImaginary(), rotation.GetReal()]


def _rigid(matrix):
    # Xforms are double precision. This checks representation error, not a
    # physical calibration allowance: a scale/shear cannot be a base placement.
    vectors = [Gf.Vec3d(*(matrix[i][j] for j in range(3))) for i in range(3)]
    tolerance = 64 * math.ulp(1.0)
    if any(abs(Gf.Dot(a, b) - (i == j)) > tolerance
           for i, a in enumerate(vectors) for j, b in enumerate(vectors)) or matrix.GetDeterminant() < 0:
        raise ValueError("Physical grasp base and TCP transforms must be rigid")


def _signature(stage, root):
    """Physical bytes relative to the placement root; no authored extent cache."""
    physical = [prim for prim in Usd.PrimRange(stage.GetPrimAtPath(root), Usd.TraverseInstanceProxies())
                if prim.HasAPI(UsdPhysics.RigidBodyAPI)
                or prim.HasAPI(UsdPhysics.CollisionAPI) or prim.IsA(UsdPhysics.Joint)]
    selected = {}
    for prim in physical:
        while prim and str(prim.GetPath()).startswith(root):
            selected[str(prim.GetPath())] = prim
            prim = prim.GetParent()

    def relative(path):
        value = str(path)
        return "." + value[len(root):] if value == root or value.startswith(root + "/") else value

    rows = []
    for path, prim in sorted(selected.items()):
        attributes = {}
        for attribute in prim.GetAttributes():
            name = attribute.GetName()
            if name.startswith("drive:") or name in ("newton:angular:position", "newton:linear:position"):
                continue
            if path == root and (name.startswith("xformOp:") or name == "xformOpOrder"):
                continue
            if attribute.ValueMightBeTimeVarying():
                raise ValueError("Physical grasp calibration cannot use animated geometry")
            if (name.startswith(("physics:", "newton:", "xformOp:")) or name == "xformOpOrder"
                    or (prim.HasAPI(UsdPhysics.CollisionAPI)
                        and name in ("points", "faceVertexCounts", "faceVertexIndices", "holeIndices",
                                     "subdivisionScheme", "orientation", "size", "radius",
                                     "height", "axis"))):
                attributes[name] = str(attribute.Get())
        rows.append({"path": relative(path), "type": prim.GetTypeName(),
                     "apis": sorted(api for api in prim.GetAppliedSchemas()
                                    if not api.startswith("PhysicsDriveAPI:")),
                     "attributes": attributes,
                     "relations": {relation.GetName(): [relative(target) for target in relation.GetTargets()]
                                   for relation in prim.GetRelationships()
                                   if relation.GetName().startswith(("physics:", "newton:"))}})
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def _record(stage, config, asset, digest):
    hand, tcp = (stage.GetPrimAtPath(config[field]) for field in ("handBody", "tcpSite"))
    if not hand or not tcp or not hand.HasAPI(UsdPhysics.RigidBodyAPI):
        raise ValueError("Physical grasp hand or TCP is missing")
    owner = _owner(tcp)
    return {"assetId": asset["id"], "assetContentHash": asset["contentHash"],
            "handBody": str(hand.GetPath()), "tcpSite": str(tcp.GetPath()),
            "siteOwner": str(owner.GetPath()), "handToTcpPose": _pose(tcp, owner),
            "geometryDigest": digest}


def physical_calibration(stage, binding, program, authored, graph):
    """Require both a pinned authoring template and its unchanged physical USD."""
    entity, config = binding["key"], binding["config"]
    matches = [item for item in authored["entities"]
               if fnmatchcase(entity, item.get("match") or item.get("key", ""))]
    if len(matches) != 1 or not matches[0].get("template"):
        raise ValueError("Nonzero grasp height requires a pinned asset Binding Template")
    asset = graph.assets.get(matches[0]["template"])
    if asset is None:
        raise ValueError("Physical grasp template asset is unavailable")
    verify_snapshot(asset)
    implementations = [item for item in asset["metadata"].get("implements", [])
                       if item["type"] == binding["type"]
                       and item.get("programs", {}).get(program.get("source")) == binding["program"]]
    if len(implementations) != 1:
        raise ValueError("Physical grasp template implementation is ambiguous")
    template = implementations[0].get("config", {})
    canonical_config = {}
    for field in ("handBody", "tcpSite", "fingerKeys", "jointKeys"):
        value = template.get(field)
        if value is None:
            raise ValueError("Physical grasp template calibration is missing: " + field)
        expected = ([rebase(item, entity) for item in value]
                    if isinstance(value, list) else rebase(value, entity))
        if config.get(field) != expected:
            raise ValueError("Physical grasp template calibration overridden: " + field)
        canonical_config[field] = ([rebase(item, "/Asset") for item in value]
                                   if isinstance(value, list) else rebase(value, "/Asset"))
    layer = graph.layer(asset, asset["metadata"]["stage"])
    if not any(spec.layer.identifier == layer.identifier and str(spec.path) == "/Asset"
               for spec in stage.GetPrimAtPath(entity).GetPrimStack()):
        raise ValueError("Physical grasp robot does not reference its pinned asset")
    canonical = Usd.Stage.Open(layer)
    _rigid(UsdGeom.XformCache().GetLocalToWorldTransform(stage.GetPrimAtPath(entity)))
    expected_digest, observed_digest = _signature(canonical, "/Asset"), _signature(stage, entity)
    if expected_digest != observed_digest:
        raise ValueError("Physical grasp calibration differs from pinned body/joint/collision geometry")
    expected = _record(canonical, canonical_config, asset, expected_digest)
    observed = _record(stage, config, asset, observed_digest)
    for field in ("handBody", "tcpSite", "siteOwner"):
        expected[field] = rebase(expected[field], entity)
    # Reuse the same strict physical identity check as P6; no pass flag is trusted.
    endpoint = resolve_endpoint(program["fields"], config, program.get("configSchema", {}))
    require_calibration(endpoint, expected, observed)
    return {"expected": expected, "composed": observed}


def tube_bounds(stage, key):
    """Local collision bounds from actual vertices or exact analytic support."""
    body = stage.GetPrimAtPath(key)
    if not body or not body.HasAPI(UsdPhysics.RigidBodyAPI):
        raise ValueError("Grasp tube is not an actual rigid body: " + key)
    points = []
    for prim in Usd.PrimRange(body, Usd.TraverseInstanceProxies()):
        if not prim.HasAPI(UsdPhysics.CollisionAPI) or _owner(prim) != body:
            continue
        if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
            continue
        matrix, reset = UsdGeom.XformCache().ComputeRelativeTransform(prim, body)
        if reset or any(attr.ValueMightBeTimeVarying() for attr in prim.GetAttributes()):
            raise ValueError("Grasp tube collision geometry cannot reset or animate")
        if prim.IsA(UsdGeom.Mesh):
            vertices = UsdGeom.Mesh(prim).GetPointsAttr().Get()
            if not vertices:
                raise ValueError("Grasp tube collision Mesh has no actual points")
            points.extend(matrix.Transform(Gf.Vec3d(vertex)) for vertex in vertices)
        elif prim.IsA(UsdGeom.Cube):
            half = UsdGeom.Cube(prim).GetSizeAttr().Get() / 2
            points.extend(matrix.Transform(Gf.Vec3d(*vertex))
                          for vertex in itertools.product((-half, half), repeat=3))
        elif prim.IsA(UsdGeom.Cylinder) or prim.IsA(UsdGeom.Capsule) or prim.IsA(UsdGeom.Sphere):
            shape = UsdGeom.Sphere(prim) if prim.IsA(UsdGeom.Sphere) else (
                UsdGeom.Capsule(prim) if prim.IsA(UsdGeom.Capsule) else UsdGeom.Cylinder(prim))
            radius = shape.GetRadiusAttr().Get()
            axis = 2 if prim.IsA(UsdGeom.Sphere) else "XYZ".index(shape.GetAxisAttr().Get())
            height = 0 if prim.IsA(UsdGeom.Sphere) else shape.GetHeightAttr().Get()
            origin = matrix.Transform(Gf.Vec3d(0))
            extents = []
            for column in range(3):
                radial = [matrix[row][column] for row in range(3)
                          if row != axis or not prim.IsA(UsdGeom.Cylinder)]
                extents.append(radius * math.hypot(*radial) + height / 2 * abs(matrix[axis][column]))
            points.extend((origin - Gf.Vec3d(*extents), origin + Gf.Vec3d(*extents)))
        else:
            raise ValueError("Grasp tube collision bounds are unsupported: " + prim.GetTypeName())
    if not points or not all(math.isfinite(value) for point in points for value in point):
        raise ValueError("Grasp tube collision bounds are unavailable or nonfinite")
    bounds = [[min(point[i] for point in points) for i in range(3)],
              [max(point[i] for point in points) for i in range(3)]]
    # Mesh points are Vec3f in USD. Two float32 roundings at this body's scale
    # cover the observed .129 -> .1289999783 conversion, not millimetres of geometry.
    representation = max(abs(value) for point in bounds for value in point) * 2 ** -22
    if abs(bounds[0][2]) > representation or bounds[1][2] <= representation:
        raise ValueError("Grasp tube origin must be its actual collision bottom with local +Z height")
    return {"boundsM": bounds, "representationM": representation}


def grasp_checks(stage, bindings, programs, authored, graph, resources=None):
    """Private calibration receipts and S5 failures for effective arm bindings."""
    problems, calibrations, tubes = [], {}, {}
    for binding in bindings:
        program = programs.get(binding["program"], {})
        fields = program.get("fields", {})
        if (not any(role.get("posture") for role in fields.values())
                and not (program.get("type") == "robot-arm/v1"
                         and "graspHeightM" in program.get("configSchema", {}).get("properties", {}))):
            continue
        entity, config = binding["key"], binding["config"]
        if (not any("postureEndpoint" in role for role in fields.values())
                and not config.get("graspHeightM")
                and "graspHeightM" not in program.get("configSchema", {}).get("properties", {})):
            continue  # Pre-height fixture programs retain their original S5 meaning.
        try:
            synthetic = program.get("source") == "synthetic"
            height = (validate_height(config.get("graspHeightM", 0.0)) if synthetic
                      else resolve_endpoint(fields, config, program.get("configSchema", {})).height)
            for key in config.get("tubes", []):
                if key in tubes and tubes[key] != height and (tubes[key] or height):
                    raise ValueError("Conflicting arm grasp heights for shared tube: " + key)
                tubes[key] = height
            if height and synthetic:
                for key in config.get("tubes", []):
                    actual = (resources or {}).get("tubeHeights", {}).get(key)
                    if actual is None or height > actual:
                        raise ValueError("Grasp height exceeds actual Synthetic tube height or is unknown: "
                                         + key)
            elif height:
                if stage is None:
                    raise ValueError("Nonzero grasp height requires actual USD tube/calibration inspection")
                calibration = physical_calibration(stage, binding, program, authored, graph)
                calibration["tubes"] = {}
                for key in config.get("tubes", []):
                    geometry = tube_bounds(stage, key)
                    if height > geometry["boundsM"][1][2] + geometry["representationM"]:
                        raise ValueError("Grasp height exceeds actual tube collision height: " + key)
                    calibration["tubes"][key] = geometry
                calibrations[entity] = calibration
        except (ValueError, KeyError, TypeError) as error:
            problems.append(entity + ": " + str(error))
    return problems, calibrations
