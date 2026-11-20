"""Import a confined USD articulation using the platform's canonical bridge."""

import math
import xml.etree.ElementTree as ET

from pxr import UsdGeom, UsdPhysics, UsdShade

from apps.node.usd_mjcf import bound_contact_material, derive_usd_mjcf
from packages.library.archive import invalid
from packages.library.robot_import import mjcf
from packages.library.structural import USDGraph


class ConfinedRobotUSD(USDGraph):
    def __init__(self, source):
        super().__init__({"path": str(source.root),
                      "files": {name: str(source.root / name) for name in source.files}}, [])
        self.active = set()

    def layer(self, owner, filename):
        if filename in self.active or len(self.active) >= 16:
            raise ValueError("Cyclic or excessively deep USD layer references")
        with open(owner["files"][filename], "rb") as stream:
            header = stream.read(8)
        if not (header.startswith(b"#usda ") or header == b"PXR-USDC"):
            raise ValueError("Only USDA and USDC layers are supported")
        self.active.add(filename)
        try:
            return super().layer(owner, filename)
        finally:
            self.active.remove(filename)


def mass_fields(prim):
    api = UsdPhysics.MassAPI(prim)
    if not api:
        return {}
    path = str(prim.GetPath())
    fields = {}
    for name, attr in (("mass", api.GetMassAttr()), ("density", api.GetDensityAttr())):
        value = attr.Get() or 0.
        if not math.isfinite(value) or value < 0:
            raise invalid(f"USD {name} must be finite and nonnegative", path=path)
        if value:
            fields[name] = value
    center = api.GetCenterOfMassAttr().Get()
    if center is not None and tuple(center) != (-math.inf,) * 3:
        if not all(math.isfinite(value) for value in center):
            raise invalid("USD centerOfMass must be finite", path=path)
        fields["centerOfMass"] = center
    diagonal = api.GetDiagonalInertiaAttr().Get()
    if diagonal is not None and tuple(diagonal) != (0., 0., 0.):
        if not all(math.isfinite(value) and value > 0 for value in diagonal):
            raise invalid("USD diagonalInertia must contain finite positive moments", path=path)
        fields["diagonalInertia"] = diagonal
    axes = api.GetPrincipalAxesAttr().Get()
    if axes is not None:
        quaternion = (axes.GetReal(), *axes.GetImaginary())
        if not all(math.isfinite(value) for value in quaternion):
            raise invalid("USD principalAxes must be finite", path=path)
        norm = math.hypot(*quaternion)
        if norm > 1e-12:
            fields["principalAxes"] = tuple(value / norm for value in quaternion)
    return fields


def collider_density(prim, bodies):
    path = prim.GetPath()
    owner = path
    while not owner.IsAbsoluteRootPath() and str(owner) not in bodies:
        owner = owner.GetParentPath()
    body_fields = bodies.get(str(owner), {})
    if path != owner:
        fields = mass_fields(prim)
        unsupported = fields.keys() - {"density"}
        if not any(fields.get("principalAxes", (1., 0., 0., 0.))[1:]):
            unsupported.discard("principalAxes")
        # A collider's COM and mass can still change the inferred inertia or COM.
        if "diagonalInertia" in body_fields:
            unsupported -= {"diagonalInertia", "principalAxes"}
            if "centerOfMass" in body_fields:
                unsupported.discard("centerOfMass")
                if "mass" in body_fields:
                    unsupported.discard("mass")
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise invalid(f"USD collider {names} cannot be preserved by robot import", path=str(path))
        if "density" in fields:
            return fields["density"]

    # OpenUSD resolves density from the collider, the rigid body, then the bound physics material.
    if "density" in body_fields:
        return None
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial("physics")
    api = UsdPhysics.MaterialAPI(material.GetPrim()) if material else None
    density = (api.GetDensityAttr().Get() or 0.) if api else 0.
    if not math.isfinite(density) or density < 0:
        raise invalid("USD physics material density must be finite and nonnegative", path=str(path))
    return density or None


def load(source, entry, meters_per_unit=None):
    graph = ConfinedRobotUSD(source)
    stage = graph.compose(entry)
    if stage is None or graph.errors:
        raise invalid("; ".join(graph.errors) or "USD dependencies cannot be composed", path=entry)
    root = stage.GetDefaultPrim()
    if not root:
        raise invalid("USD robot must declare a default prim", path=entry)
    if meters_per_unit is not None:
        UsdGeom.SetStageMetersPerUnit(stage, meters_per_unit)
    units = (UsdGeom.GetStageMetersPerUnit(stage), UsdPhysics.GetStageKilogramsPerUnit(stage))
    if not all(math.isfinite(value) and value > 0 for value in units):
        raise invalid("USD unit metadata must be finite and positive", path=entry)
    derived = derive_usd_mjcf(
        stage, prim_path=str(root.GetPath()), include_static_colliders=False, source_units=True,
    )
    # The canonical bridge encodes a fixed articulation root as a mocap body.
    # It serves as a fixed root in the intermediate representation.
    xml = ET.fromstring(derived.mjcf_xml)
    if derived.fixed_base:
        xml.find("worldbody/body").attrib.pop("mocap", None)
    meters, kilograms = units
    density_scale = kilograms / meters**3
    bodies = {path: mass_fields(stage.GetPrimAtPath(path)) for path in derived.body_names_by_path}
    colliders = {}
    for path, name in derived.collider_names_by_path.items():
        prim = stage.GetPrimAtPath(path)
        mesh = UsdPhysics.MeshCollisionAPI(prim)
        approximation = mesh.GetApproximationAttr().Get() if mesh else None
        contact = bound_contact_material(prim)
        density = collider_density(prim, bodies)
        colliders[name] = (approximation == "convexHull", contact[1] if contact else 0.,
                           density * density_scale if density else None)
    robot = mjcf.compiled(ET.tostring(xml, encoding="unicode"), entry, usd_colliders=colliders)
    robot.up = str(UsdGeom.GetStageUpAxis(stage))
    body_paths = {name: path for path, name in derived.body_names_by_path.items()}
    for name, body in robot.bodies.items():
        fields = bodies[body_paths[name]]
        if "mass" not in fields:
            body.mass = None
        if "centerOfMass" not in fields:
            body.center = None
        if "diagonalInertia" not in fields:
            body.inertia = None
            body.inferred_principal_axes = fields.get("principalAxes")
        if "density" in fields:
            body.density = fields["density"] * density_scale
    source_joints = {joint.name: joint for joint in derived.joints}
    for joint in robot.joints:
        if joint.name in source_joints:
            authored = source_joints[joint.name]
            joint.initial = (authored.initial_position if authored.initial_position is not None
                             else authored.default_position)
            joint.name = authored.prim_path
    return robot
