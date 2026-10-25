"""Declarative Scene Factory plans, procedural packages and canonical scene packages.

This is the pure-Python (no ``bpy``) lane. A plan pins exact local Asset versions,
declares instances in metres/Z-up and points at procedural object parameters. The
factory writes immutable procedural asset packages, a canonical scene package that
references trusted packages with ``@library:`` paths, and a recipe record. It also
consumes a Blender layout manifest produced by ``factory/blender_author.py``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pxr import Usd, UsdPhysics
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from factory.procedural import (
    ProceduralError,
    package_digest,
    rack_geometry,
    rack_mesh_json,
    rack_package,
    tube_geometry,
    tube_mesh_json,
    tube_package,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_LIBRARY = REPO_ROOT / "library"
RECIPE = "sf3-blender-factory-v2"
PLAN_SCHEMA = "lw.scene-plan/1"
LAYOUT_SCHEMA = "lw.scene-layout/1"
REQUEST_SCHEMA = "lw.blender-authoring/1"
BUILD_SCHEMA = "lw.scene-factory-build/1"
BLENDER_VERSION = "4.5.9"

KINDS = ("robot", "device", "labware", "environment")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
IDENTITY = re.compile(r"([a-z0-9]+(?:-[a-z0-9]+)*)/([a-z0-9]+(?:-[a-z0-9]+)*)")
REFERENCE = re.compile(r"([a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)")
ENTITY = re.compile(r"/World/Lab(?:/[A-Za-z][A-Za-z0-9_]*)+")
INSTANCE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
HASH = re.compile(r"[0-9a-f]{64}")
SCENE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class PlanError(ValueError):
    """The plan is incomplete, guessed, or outside the factory's supported shape."""


class FactoryError(RuntimeError):
    """The requested output cannot be produced without changing an immutable package."""


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _ref(value):
    match = REFERENCE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("asset must be an exact <kind>/<name>@<positive integer> reference")
    if match.group(1).partition("/")[0] not in KINDS:
        raise ValueError("asset kind must be robot, device, labware or environment")
    return match.group(1), int(match.group(2))


def _vector(value, *, fields=("x", "y", "z")):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("expected three coordinates")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("coordinates must be finite numbers")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError("coordinates must be finite numbers")
        result.append(number)
    return result


class Units(Schema):
    length: Literal["m"]
    upAxis: Literal["Z"]
    metersPerUnit: Literal[1]


class Transform(Schema):
    positionM: list[float]
    rotationDeg: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])

    @field_validator("positionM", "rotationDeg")
    @classmethod
    def finite_vector(cls, value):
        return _vector(value)


class AssetPin(Schema):
    id: str
    version: StrictInt = Field(ge=1)
    contentHash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("id")
    @classmethod
    def identity(cls, value):
        match = IDENTITY.fullmatch(value)
        if match is None or match.group(1) not in KINDS:
            raise ValueError("id must be an exact <kind>/<name> without a revision")
        return value

    @property
    def ref(self):
        return f"{self.id}@{self.version}"


class RackParameters(Schema):
    widthM: float
    depthM: float
    heightM: float
    wallThicknessM: float
    baseThicknessM: float
    plateThicknessM: float
    holeDiameterM: float
    tubeDiameterM: float
    clearanceM: float
    pitchXM: float
    pitchYM: float
    rows: Literal[2] = 2
    columns: Literal[3] = 3
    collision: Literal["solid", "holes"]
    massDensityKgM3: float = 700.0


class TubeParameters(Schema):
    diameterM: float
    heightM: float
    massDensityKgM3: float = 1200.0


class ProceduralObject(Schema):
    id: str
    version: StrictInt = Field(ge=1)
    kind: Literal["environment", "labware"]
    type: Literal["six-hole-tube-rack", "test-tube"]
    seed: StrictInt = Field(default=0, ge=0)
    license: Literal["CC0-1.0"] = "CC0-1.0"
    rack: RackParameters | None = None
    tube: TubeParameters | None = None

    @field_validator("id")
    @classmethod
    def identity(cls, value):
        match = IDENTITY.fullmatch(value)
        if match is None or match.group(1) not in KINDS:
            raise ValueError("id must be an exact <kind>/<name> without a revision")
        return value

    @model_validator(mode="after")
    def shape(self):
        expected = {
            "six-hole-tube-rack": ("environment", self.rack, self.tube),
            "test-tube": ("labware", self.tube, self.rack),
        }
        kind, present, absent = expected[self.type]
        if self.kind != kind:
            raise ValueError(f"{self.type} requires kind: {kind}")
        if present is None or absent is not None:
            raise ValueError(f"{self.type} requires exactly its matching parameter block")
        try:
            if self.type == "six-hole-tube-rack":
                rack_geometry(self.rack.model_dump())
            else:
                tube_geometry(self.tube.model_dump())
        except ProceduralError as exc:
            raise ValueError(str(exc)) from exc
        return self

    @property
    def ref(self):
        return f"{self.id}@{self.version}"


class Seat(Schema):
    rack: str
    hole: str


class Binding(Schema):
    template: str
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("template")
    @classmethod
    def template_ref(cls, value):
        _ref(value)
        return value


class Instance(Schema):
    instanceId: str
    entityKey: str
    asset: str
    transform: Transform
    seat: Seat | None = None
    binding: Binding | None = None
    initialJointPositions: dict[str, float] = Field(default_factory=dict)
    kinematic: bool = False

    @field_validator("instanceId")
    @classmethod
    def instance_name(cls, value):
        if INSTANCE.fullmatch(value) is None:
            raise ValueError("instanceId must be lowercase kebab-case")
        return value

    @field_validator("initialJointPositions")
    @classmethod
    def joint_positions(cls, value):
        for name, position in value.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_/]*", name):
                raise ValueError("joint names must be relative USD prim names")
            if isinstance(position, bool) or not isinstance(position, (int, float)):
                raise ValueError("joint positions must be finite numbers")
            if not math.isfinite(float(position)):
                raise ValueError("joint positions must be finite numbers")
        return {name: float(position) for name, position in value.items()}

    @field_validator("entityKey")
    @classmethod
    def entity_key(cls, value):
        if ENTITY.fullmatch(value) is None:
            raise ValueError("entityKey must be an ASCII /World/Lab/<Name> prim path")
        if value == "/World/Lab":
            raise ValueError("entityKey must name an entity below /World/Lab")
        return value

    @field_validator("asset")
    @classmethod
    def asset_ref(cls, value):
        _ref(value)
        return value


class LocationEntry(Schema):
    site: str | None = None
    zone: str | None = None
    p: list[float] | None = None
    approach: list[float] | None = None
    grasp: list[float] | None = None
    requires: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("p", "approach", "grasp")
    @classmethod
    def finite_optional(cls, value):
        return None if value is None else _vector(value)


class LocationsSpec(Schema):
    robot: str | None = None
    locations: dict[str, LocationEntry] = Field(default_factory=dict)


class Simulation(Schema):
    solver: Literal["xpbd", "mujoco", "featherstone"] | None = None
    warmupSteps: StrictInt | None = Field(default=None, gt=0)


class Dressing(Schema):
    name: str = "Bench"
    sizeM: list[float]
    positionM: list[float]
    color: list[float] = Field(default_factory=lambda: [0.55, 0.58, 0.61])

    @field_validator("sizeM", "positionM", "color")
    @classmethod
    def finite_vector(cls, value):
        return _vector(value)

    @model_validator(mode="after")
    def positive(self):
        if any(value <= 0 for value in self.sizeM):
            raise ValueError("dressing sizeM must be positive")
        return self


class SceneSpec(Schema):
    name: str
    title: str = Field(min_length=1)
    description: str = ""
    sources: list[Literal["newton"]] = Field(default_factory=lambda: ["newton"])
    simulation: Simulation | None = None
    locations: LocationsSpec = Field(default_factory=LocationsSpec)
    dressing: list[Dressing] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def scene_name(cls, value):
        if SCENE_NAME.fullmatch(value) is None:
            raise ValueError("scene name must be lowercase kebab-case")
        return value


class Plan(Schema):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)

    schema_id: Literal["lw.scene-plan/1"] = Field(alias="schema")
    recipe: Literal["sf3-blender-factory-v1", "sf3-blender-factory-v2"]
    buildLabel: str = ""
    units: Units
    scene: SceneSpec
    assets: list[AssetPin] = Field(default_factory=list)
    procedural: list[ProceduralObject] = Field(default_factory=list)
    instances: list[Instance] = Field(min_length=1)

    @model_validator(mode="after")
    def complete(self):
        _validate_plan(self)
        return self


def _validate_plan(plan):
    trusted = {pin.ref: pin for pin in plan.assets}
    procedural = {item.ref: item for item in plan.procedural}
    duplicate = set(trusted) & set(procedural)
    if duplicate:
        raise ValueError("asset is both trusted and procedural: " + ", ".join(sorted(duplicate)))
    pins = {**trusted, **procedural}
    if len(pins) != len(plan.assets) + len(plan.procedural):
        raise ValueError("asset pins must have unique kind/name@version identities")
    used = set()
    keys, ids = set(), set()
    for instance in plan.instances:
        if instance.instanceId in ids:
            raise ValueError("duplicate instanceId: " + instance.instanceId)
        if instance.entityKey in keys:
            raise ValueError("duplicate entityKey: " + instance.entityKey)
        ids.add(instance.instanceId)
        keys.add(instance.entityKey)
        if instance.asset not in pins:
            raise ValueError(
                "instance references an undeclared asset version: " + instance.asset
            )
        used.add(instance.asset)
        if instance.binding is not None and instance.binding.template != instance.asset:
            raise ValueError("binding template must equal the exact instance asset version")
        if instance.binding is None and procedural.get(instance.asset) is not None:
            if procedural[instance.asset].type != "six-hole-tube-rack":
                raise ValueError("a procedural object used as an entity requires a binding")
        if instance.kinematic and (
            procedural.get(instance.asset) is not None
            or instance.asset.partition("/")[0] == "robot"
        ):
            raise ValueError("kinematic overrides apply only to non-robot trusted props")
    for key in sorted(keys):
        for other in keys:
            if key != other and other.startswith(key + "/"):
                raise ValueError("entityKey instances cannot be nested: " + other)
    if set(pins) != used:
        unused = sorted(set(pins) - used)
        raise ValueError("declared asset versions are not referenced: " + ", ".join(unused))
    kind_by_ref = {
        ref: pin.id.partition("/")[0] for ref, pin in trusted.items()
    } | {ref: item.kind for ref, item in procedural.items()}
    type_by_ref = {ref: item.type for ref, item in procedural.items()}
    for instance in plan.instances:
        if instance.initialJointPositions and kind_by_ref[instance.asset] != "robot":
            raise ValueError("initialJointPositions apply only to trusted robot instances")
    dressing_names = [item.name for item in plan.scene.dressing]
    if len(dressing_names) != len(set(dressing_names)):
        raise ValueError("dressing names must be unique")
    for name in dressing_names:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            raise ValueError("dressing names must be ASCII USD prim names")
        if any(instance.entityKey == f"/World/Dressing/{name}" for instance in plan.instances):
            raise ValueError("dressing name collides with an instance path")
    rack_instances = {
        instance.instanceId: (instance, procedural[instance.asset])
        for instance in plan.instances
        if procedural.get(instance.asset) is not None
        and procedural[instance.asset].type == "six-hole-tube-rack"
    }
    robot_keys = {
        instance.entityKey
        for instance in plan.instances
        if kind_by_ref[instance.asset] == "robot"
    }
    tube_keys = {
        instance.entityKey
        for instance in plan.instances
        if type_by_ref.get(instance.asset) == "test-tube"
    }
    site_keys = set(robot_keys)
    for _, (instance, spec) in rack_instances.items():
        for name, _, _ in rack_geometry(spec.rack.model_dump()).sites:
            site_keys.add(f"{instance.entityKey}/{name}")
    if plan.scene.locations.robot is not None and plan.scene.locations.robot not in robot_keys:
        raise ValueError("locations.robot must name a declared robot instance")
    for name, location in plan.scene.locations.locations.items():
        if location.site is not None and location.site not in site_keys:
            raise ValueError(f"location {name} references an unknown Site: {location.site}")
    for instance in plan.instances:
        if instance.seat is not None:
            _validate_seat(instance, plan, rack_instances)
        if instance.binding is None:
            continue
        config = instance.binding.config
        locations = config.get("locations")
        if locations is not None and locations != "locations.yaml":
            raise ValueError("binding locations must name the generated locations.yaml")
        for field in ("locationSites",):
            values = config.get(field)
            if values is None:
                continue
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise ValueError(f"{field} must be a list of entity paths")
            missing = sorted(set(values) - site_keys)
            if missing:
                raise ValueError(f"{field} references unknown Sites: " + ", ".join(missing))
        for field, known in (("robots", robot_keys), ("tubes", tube_keys)):
            values = config.get(field)
            if values is None:
                continue
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise ValueError(f"{field} must be a list of entity keys")
            missing = sorted(set(values) - known)
            if missing:
                raise ValueError(f"{field} references unknown entities: " + ", ".join(missing))


def _euler_matrix(rotation):
    rx, ry, rz = (math.radians(value) for value in rotation)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    return (
        (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
        (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
        (-sy, cy * sx, cy * cx),
    )


def _seat_world(rack: Instance, x, y, z):
    matrix = _euler_matrix(rack.transform.rotationDeg)
    point = (x, y, z)
    return [
        rack.transform.positionM[index] + sum(matrix[index][column] * point[column] for column in range(3))
        for index in range(3)
    ]


def _validate_seat(instance, plan, rack_instances):
    if instance.seat.rack not in rack_instances:
        raise ValueError("seat must name a procedural six-hole-tube-rack instance")
    rack, spec = rack_instances[instance.seat.rack]
    layout = rack_geometry(spec.rack.model_dump())
    holes = {name: (x, y) for name, x, y in layout.sites}
    hole = instance.seat.hole.removeprefix("Hole")
    if f"Hole{hole}" not in holes:
        raise ValueError(f"unknown rack hole: {instance.seat.hole}")
    x, y = holes[f"Hole{hole}"]
    expected = _seat_world(rack, x, y, spec.rack.baseThicknessM)
    if any(abs(a - b) > 1e-8 for a, b in zip(instance.transform.positionM, expected, strict=True)):
        raise ValueError(
            "seated instance position does not match the rack hole transform: "
            + instance.instanceId
        )
    if abs(instance.transform.rotationDeg[0]) > 1e-9 or abs(instance.transform.rotationDeg[1]) > 1e-9:
        raise ValueError("a seated object must stay upright")


def load_plan(path):
    path = Path(path)
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PlanError(f"cannot read plan: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanError("plan must be a mapping")
    try:
        return Plan.model_validate(value)
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            problems.append(f"{location}: {error['msg']}")
        raise PlanError("invalid scene plan: " + "; ".join(problems)) from exc
    except ValueError as exc:
        raise PlanError("invalid scene plan: " + str(exc)) from exc


def plan_digest(plan):
    payload = plan.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _package_digest(path):
    digest = hashlib.sha256()
    root = Path(path)
    try:
        entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    except OSError as exc:
        raise FactoryError(f"cannot inspect package: {exc}") from exc
    for entry in entries:
        if entry.is_symlink() or not entry.resolve().is_relative_to(root.resolve()):
            raise FactoryError(f"package contains a link outside itself: {entry}")
        if entry.is_file():
            digest.update(entry.relative_to(root).as_posix().encode("utf-8") + b"\0")
            with entry.open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


def _library_assets(plan, roots):
    result = {}
    for pin in plan.assets:
        found = []
        for root in roots:
            candidate = Path(root).resolve() / "assets" / pin.id / str(pin.version)
            if (candidate / "asset.yaml").is_file():
                found.append(candidate)
        if not found:
            raise PlanError(f"trusted asset version is not installed: {pin.ref}")
        if len(found) != 1:
            raise PlanError(f"trusted asset version is ambiguous across library roots: {pin.ref}")
        path = found[0]
        try:
            metadata = yaml.safe_load((path / "asset.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PlanError(f"cannot read trusted asset metadata: {pin.ref}") from exc
        if not isinstance(metadata, dict) or metadata.get("id") != pin.id:
            raise PlanError("trusted asset metadata does not match its pinned identity: " + pin.ref)
        stage = metadata.get("stage")
        stage_path = Path(stage) if isinstance(stage, str) else None
        if (
            stage_path is None
            or stage_path.is_absolute()
            or "\\" in stage
            or ".." in stage_path.parts
            or stage_path.suffix not in (".usd", ".usda", ".usdc")
        ):
            raise PlanError("trusted asset does not declare a package-local USD stage: " + pin.ref)
        stage_file = (path / stage_path).resolve()
        if not stage_file.is_file() or not stage_file.is_relative_to(path.resolve()):
            raise PlanError("trusted asset stage file is missing or outside its package: " + pin.ref)
        digest = _package_digest(path)
        if pin.contentHash is not None and digest != pin.contentHash:
            raise PlanError("trusted asset content hash differs from the pin: " + pin.ref)
        entry = {
            "id": pin.ref,
            "source": "library",
            "path": str(path),
            "stage": stage,
            "contentHash": digest,
            "kind": pin.id.partition("/")[0],
        }
        if entry["kind"] == "robot":
            entry["joints"] = _stage_joints(path / stage)
        else:
            entry["bodies"] = _stage_bodies(path / stage)
        result[pin.ref] = entry
    return result


def _stage_bodies(stage_path):
    stage = Usd.Stage.Open(str(stage_path))
    if stage is None:
        raise PlanError("trusted asset USD stage cannot be read: " + str(stage_path))
    bodies = []
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            path = str(prim.GetPath())
            bodies.append("" if path == "/Asset" else path.removeprefix("/Asset/"))
    return bodies


def _stage_joints(stage_path):
    """Inspect trusted robot joints without re-exporting or rewriting their package."""
    stage = Usd.Stage.Open(str(stage_path))
    if stage is None:
        raise PlanError("trusted robot USD stage cannot be read: " + str(stage_path))
    joints = {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        name = str(prim.GetPath())
        name = "" if name == "/Asset" else name.removeprefix("/Asset/")
        if prim.IsA(UsdPhysics.RevoluteJoint):
            joint = UsdPhysics.RevoluteJoint(prim)
            joints[name] = {
                "kind": "revolute",
                "attribute": "newton:angular:position",
                "lower": joint.GetLowerLimitAttr().Get(),
                "upper": joint.GetUpperLimitAttr().Get(),
                "factor": math.pi / 180,
            }
        elif prim.IsA(UsdPhysics.PrismaticJoint):
            joint = UsdPhysics.PrismaticJoint(prim)
            joints[name] = {
                "kind": "prismatic",
                "attribute": "newton:linear:position",
                "lower": joint.GetLowerLimitAttr().Get(),
                "upper": joint.GetUpperLimitAttr().Get(),
                "factor": 1.0,
            }
        else:
            joints[name] = {"kind": "other"}
    return joints


def _joint_overrides(instance, asset):
    if not instance.initialJointPositions:
        return {}
    joints = asset.get("joints") or {}
    overrides = {}
    for name, value in instance.initialJointPositions.items():
        information = joints.get(name)
        if information is None:
            raise PlanError(f"robot asset has no joint {name}: {instance.asset}")
        if information["kind"] not in ("revolute", "prismatic"):
            raise PlanError("initial position is supported only for scalar robot joints: " + name)
        lower, upper = information["lower"], information["upper"]
        valid = (
            isinstance(lower, (int, float))
            and isinstance(upper, (int, float))
            and lower <= value / information["factor"] <= upper
        )
        if not valid:
            raise PlanError(f"initial joint position is outside the trusted limits: {name}")
        # Newton authored angular positions are degrees; the plan uses radians.
        overrides[name] = ("double", information["attribute"], value / information["factor"])
    return overrides


def _kinematic_overrides(instance, asset):
    if not instance.kinematic:
        return {}
    bodies = asset.get("bodies") or []
    if not bodies:
        raise PlanError("kinematic override requires an authored rigid body: " + instance.asset)
    return {
        body: ("bool", "physics:kinematicEnabled", True)
        for body in bodies
    }


def _override_value(kind, value):
    if kind == "bool":
        return "true" if value else "false"
    return _fmt(value)


def _fmt(value):
    number = float(value)
    if abs(number) < 5e-13:
        number = 0.0
    text = f"{number:.9g}"
    return "0" if text == "-0" else text


def _tuple(values):
    return "(" + ", ".join(_fmt(value) for value in values) + ")"


def _override_text(overrides, depth):
    tree = {}
    direct = {}
    for name, (kind, attribute, value) in overrides.items():
        if not name:
            direct[attribute] = (kind, value)
            continue
        node = tree
        for part in name.split("/"):
            node = node.setdefault(part, {})
        node.setdefault("__attrs__", {})[attribute] = (kind, value)

    def render(node, level):
        pad = " " * level
        text = ""
        for name, child in node.items():
            if name == "__attrs__":
                continue
            text += f'{pad}over "{name}"\n{pad}{{\n'
            for attribute, (kind, value) in child.get("__attrs__", {}).items():
                text += f"{pad}    {kind} {attribute} = {_override_value(kind, value)}\n"
            text += render(child, level + 4)
            text += f"{pad}}}\n"
        return text

    direct_text = "".join(
        f"{' ' * depth}{kind} {attribute} = {_override_value(kind, value)}\n"
        for attribute, (kind, value) in direct.items()
    )
    return direct_text, render(tree, depth)


def _stage(plan, transforms, assets):
    tree = {"children": {}, "instance": None}
    for instance in plan.instances:
        node = tree
        for part in instance.entityKey.split("/")[3:]:
            node = node["children"].setdefault(part, {"children": {}, "instance": None})
        node["instance"] = instance

    def render(node, depth):
        pad = " " * depth
        text = ""
        for name, child in node["children"].items():
            if child["instance"] is None:
                text += f'{pad}def Xform "{name}"\n{pad}{{\n'
                text += render(child, depth + 4)
                text += f"{pad}}}\n"
                continue
            instance = child["instance"]
            asset = assets[instance.asset]
            reference = (
                "library:assets/"
                + instance.asset.replace("@", "/")
                + "/"
                + asset["stage"]
            )
            transform = transforms[instance.instanceId]
            overrides = _joint_overrides(instance, asset) | _kinematic_overrides(instance, asset)
            direct, nested = _override_text(overrides, depth + 4) if overrides else ("", "")
            text += (
                f'{pad}def Xform "{name}" (\n'
                f"{pad}    prepend references = @{reference}@</Asset>\n"
                f"{pad})\n"
                f"{pad}{{\n"
                f"{pad}    double3 xformOp:translate = {_tuple(transform.positionM)}\n"
                f"{pad}    float3 xformOp:rotateXYZ = {_tuple(transform.rotationDeg)}\n"
                f'{pad}    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:rotateXYZ"]\n'
            )
            if direct:
                text += direct
            text += nested
            text += f"{pad}}}\n"
        return text

    dressing = ""
    if plan.scene.dressing:
        dressing = '    def Xform "Dressing"\n    {\n'
        for item in plan.scene.dressing:
            dressing += (
                f'        def Cube "{item.name}"\n'
                "        {\n"
                "            double size = 1\n"
                f"            float3 xformOp:scale = {_tuple(item.sizeM)}\n"
                f"            double3 xformOp:translate = {_tuple(item.positionM)}\n"
                '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]\n'
                f"            color3f[] primvars:displayColor = [( "
                + ", ".join(_fmt(value) for value in item.color)
                + ' )] ( interpolation = "constant" )\n'
                "        }\n"
            )
        dressing += "    }\n"
    return (
        "#usda 1.0\n"
        "(\n"
        '    defaultPrim = "World"\n'
        "    metersPerUnit = 1\n"
        '    upAxis = "Z"\n'
        ")\n\n"
        'def Xform "World"\n'
        "{\n"
        '    def Xform "Lab"\n'
        "    {\n"
        + render(tree, 8)
        + "    }\n"
        + dressing
        + "}\n"
    )


def _yaml(value):
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).encode("utf-8")


def _scene_files(plan, transforms, assets, layout_summary):
    scene = plan.scene
    metadata = {
        "name": scene.name,
        "title": scene.title,
        "description": scene.description,
        "sources": list(scene.sources),
        "stage": "stage.usda",
        "derivedFrom": {
            "recipe": RECIPE,
            "planSha256": plan_digest(plan),
            **({"buildLabel": plan.buildLabel} if plan.buildLabel else {}),
        },
    }
    if scene.simulation is not None:
        metadata["simulation"] = scene.simulation.model_dump(exclude_none=True)
    devices = {"scene": scene.name, "entities": []}
    for instance in plan.instances:
        if instance.binding is None:
            continue
        devices["entities"].append(
            {
                "key": instance.entityKey,
                "template": instance.binding.template,
                "config": deepcopy(instance.binding.config),
            }
        )
    locations = scene.locations.model_dump(exclude_none=True)
    if not locations.get("locations"):
        locations["locations"] = {}
    procedural_by_ref = {
        item.ref: {
            "type": item.type,
            "seed": item.seed,
            "license": item.license,
            "parameters": (item.rack or item.tube).model_dump(),
        }
        for item in plan.procedural
    }
    recipe_assets, seen_assets = [], set()
    for instance in plan.instances:
        if instance.asset in seen_assets:
            continue
        seen_assets.add(instance.asset)
        entry = {
            "id": instance.asset,
            "source": assets[instance.asset]["source"],
            "contentHash": assets[instance.asset]["contentHash"],
            "stage": assets[instance.asset]["stage"],
        }
        if instance.asset in procedural_by_ref:
            entry["procedural"] = procedural_by_ref[instance.asset]
        recipe_assets.append(entry)
    recipe = {
        "schema": "lw.factory-recipe/1",
        "recipe": RECIPE,
        "planSha256": plan_digest(plan),
        "buildLabel": plan.buildLabel,
        "units": plan.units.model_dump(),
        "blender": layout_summary,
        "assets": recipe_assets,
        "instances": [
            {
                "instanceId": instance.instanceId,
                "entityKey": instance.entityKey,
                "asset": instance.asset,
                "positionM": transforms[instance.instanceId].positionM,
                "rotationDeg": transforms[instance.instanceId].rotationDeg,
                "kinematic": instance.kinematic,
            }
            for instance in plan.instances
        ],
        "dressing": [item.model_dump() for item in plan.scene.dressing],
    }
    files = {
        "scene.yaml": _yaml(metadata),
        "stage.usda": _stage(plan, transforms, assets).encode("utf-8"),
        "devices.yaml": _yaml(devices),
        "locations.yaml": _yaml(locations),
        "factory.json": (json.dumps(recipe, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    return files


def _read_package(path):
    root = Path(path)
    files = {}
    for entry in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if entry.is_symlink() or not entry.resolve().is_relative_to(root.resolve()):
            raise FactoryError(f"package contains a link outside itself: {entry}")
        if entry.is_file():
            files[entry.relative_to(root).as_posix()] = entry.read_bytes()
    return files


def _check_package(path, files):
    path = Path(path)
    if not path.exists():
        return
    if not path.is_dir():
        raise FactoryError(f"package destination is not a directory: {path}")
    if _read_package(path) != files:
        raise FactoryError(
            "refusing to overwrite an immutable factory package: " + str(path)
        )


def _same_manifest(existing, expected):
    """A manifest written with platform newline translation has the same content."""
    if existing == expected:
        return True
    try:
        return existing.decode("utf-8").replace("\r\n", "\n") == expected.decode("utf-8")
    except UnicodeDecodeError:
        return False


def _write_package(path, files):
    path = Path(path)
    if path.exists():
        if not path.is_dir():
            raise FactoryError(f"package destination is not a directory: {path}")
        existing = _read_package(path)
        if existing != files:
            raise FactoryError(
                "refusing to overwrite an immutable factory package: " + str(path)
            )
        return
    temporary = path.with_name(path.name + ".factory-tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        for name, value in files.items():
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
        temporary.rename(path)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _layout(plan, layout, expected_request_sha):
    if not isinstance(layout, dict):
        raise PlanError("layout manifest must be a mapping")
    if layout.get("schema") != LAYOUT_SCHEMA:
        raise PlanError("layout manifest has an unsupported schema")
    if layout.get("scene") != plan.scene.name:
        raise PlanError("layout manifest belongs to another scene")
    expected = plan_digest(plan)
    if layout.get("planSha256") != expected:
        raise PlanError("layout manifest belongs to another plan version")
    request_sha = layout.get("requestSha256")
    if not isinstance(request_sha, str) or HASH.fullmatch(request_sha) is None:
        raise PlanError("layout.requestSha256 must be a SHA-256 digest")
    if request_sha != expected_request_sha:
        raise PlanError("layout was produced from a different Blender authoring request")
    blender = layout.get("blender")
    if not isinstance(blender, dict):
        raise PlanError("layout requires Blender provenance")
    version = blender.get("version")
    if version != BLENDER_VERSION:
        raise PlanError(f"layout must come from Blender {BLENDER_VERSION}")
    blend = blender.get("blendSha256")
    if not isinstance(blend, str) or HASH.fullmatch(blend) is None:
        raise PlanError("blender.blendSha256 must be a SHA-256 digest")
    mode = layout.get("mode")
    if mode not in ("script", "human-edited"):
        raise PlanError("layout.mode must be script or human-edited")
    entries = layout.get("instances")
    if not isinstance(entries, list):
        raise PlanError("layout manifest requires an instances list")
    expected_ids = {instance.instanceId for instance in plan.instances}
    transforms, seen = {}, set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PlanError("layout instance must be a mapping")
        identifier = entry.get("instanceId")
        if identifier in seen:
            raise PlanError("layout contains a duplicate instance: " + str(identifier))
        if identifier not in expected_ids:
            raise PlanError("layout contains an unknown instance: " + str(identifier))
        seen.add(identifier)
        instance = next(item for item in plan.instances if item.instanceId == identifier)
        if entry.get("entityKey") != instance.entityKey:
            raise PlanError("layout changed or omitted the stable entityKey: " + str(identifier))
        if entry.get("rotationDeg") is None:
            raise PlanError("layout instance requires rotationDeg: " + str(identifier))
        try:
            transforms[identifier] = Transform.model_validate(
                {"positionM": entry.get("positionM"), "rotationDeg": entry.get("rotationDeg")}
            )
        except ValidationError as exc:
            raise PlanError("layout instance has an invalid transform: " + str(identifier)) from exc
    missing = sorted(expected_ids - seen)
    if missing:
        raise PlanError("layout is missing instances: " + ", ".join(missing))
    procedural = layout.get("procedural") or []
    if not isinstance(procedural, list):
        raise PlanError("layout procedural section must be a list")
    procedural_specs = {
        instance.instanceId: spec
        for instance in plan.instances
        for spec in plan.procedural
        if instance.asset == spec.ref
    }
    seen_procedural = set()
    for entry in procedural:
        identifier = entry.get("instanceId") if isinstance(entry, dict) else None
        if identifier not in procedural_specs:
            raise PlanError("layout procedural entry does not match a procedural instance")
        if identifier in seen_procedural:
            raise PlanError("layout contains a duplicate procedural entry")
        seen_procedural.add(identifier)
        spec = procedural_specs[identifier]
        if spec.type == "six-hole-tube-rack":
            geometry = rack_geometry(spec.rack.model_dump())
            mesh = rack_mesh_json(spec.rack.model_dump())
        else:
            geometry = tube_geometry(spec.tube.model_dump())
            mesh = tube_mesh_json(spec.tube.model_dump())
        if entry.get("type") != spec.type:
            raise PlanError("layout procedural type differs from the plan")
        if spec.type == "six-hole-tube-rack":
            if entry.get("holeCount") != len(geometry.sites):
                raise PlanError("layout rack hole count differs from the plan")
            if entry.get("collision") != spec.rack.collision:
                raise PlanError("layout rack collision mode differs from the plan")
        dimensions = entry.get("dimensionsM")
        if (
            not isinstance(dimensions, list)
            or len(dimensions) != 3
            or any(
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or abs(float(value) - expected) > 1e-4
                for value, expected in zip(dimensions, geometry.dimensions_m)
            )
        ):
            raise PlanError("layout procedural dimensions differ from the plan")
        if entry.get("meshSha256") != _mesh_sha256(mesh):
            raise PlanError("layout procedural mesh differs from the plan")
    if seen_procedural != set(procedural_specs):
        raise PlanError("layout is missing procedural instances")
    summary = {
        "mode": mode,
        "version": version,
        "blendSha256": blend,
        "requestSha256": request_sha,
    }
    return transforms, summary


def build(plan, output, *, library_roots=None, layout=None):
    """Build procedural packages, a canonical scene package and a recipe record."""
    output = Path(output).resolve()
    roots = [Path(item).resolve() for item in (library_roots or [BUILTIN_LIBRARY])]
    trusted = _library_assets(plan, roots)
    transforms = {instance.instanceId: instance.transform for instance in plan.instances}
    layout_summary = None
    if layout is not None:
        expected_request_sha = request_digest(authoring_request(plan, library_roots=roots))
        transforms, layout_summary = _layout(plan, layout, expected_request_sha)
    assets = dict(trusted)
    pending = []
    for spec in plan.procedural:
        if spec.type == "six-hole-tube-rack":
            files, geometry = rack_package(spec.model_dump())
        else:
            files, geometry = tube_package(spec.model_dump())
        destination = output / "assets" / spec.id / str(spec.version)
        pending.append((destination, files))
        assets[spec.ref] = {
            "id": spec.ref,
            "source": "procedural",
            "path": str(destination),
            "stage": "asset.usda",
            "contentHash": package_digest(files),
            "kind": spec.kind,
            "dimensionsM": list(geometry.dimensions_m),
        }
    scene = plan.scene
    scene_files = _scene_files(plan, transforms, assets, layout_summary)
    scene_path = output / "scenes" / scene.name
    # Check every destination before writing so a conflicting scene cannot leave
    # a half-published set of new asset versions.
    for destination, files in pending:
        _check_package(destination, files)
    _check_package(scene_path, scene_files)
    for destination, files in pending:
        _write_package(destination, files)
    _write_package(scene_path, scene_files)
    manifest = {
        "schema": BUILD_SCHEMA,
        "recipe": RECIPE,
        "planSha256": plan_digest(plan),
        "buildLabel": plan.buildLabel,
        "units": plan.units.model_dump(),
        "blender": layout_summary,
        "assets": [
            {
                key: assets[ref][key]
                for key in ("id", "source", "contentHash", "stage", "kind")
            }
            | ({"dimensionsM": assets[ref]["dimensionsM"]} if "dimensionsM" in assets[ref] else {})
            for ref in sorted(assets)
        ],
        "instances": [
            {
                "instanceId": instance.instanceId,
                "entityKey": instance.entityKey,
                "asset": instance.asset,
                "positionM": transforms[instance.instanceId].positionM,
                "rotationDeg": transforms[instance.instanceId].rotationDeg,
                "kinematic": instance.kinematic,
            }
            for instance in plan.instances
        ],
        "scene": {
            "name": scene.name,
            "package": f"scenes/{scene.name}",
            "stage": "stage.usda",
            "contentHash": package_digest(scene_files),
            "files": sorted(scene_files),
            "dressing": [item.model_dump() for item in scene.dressing],
        },
        "gaps": [],
    }
    manifest_path = output / "manifest.json"
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if manifest_path.is_symlink():
        raise FactoryError("manifest destination cannot be a filesystem link: " + str(manifest_path))
    if manifest_path.exists():
        if not _same_manifest(manifest_path.read_bytes(), manifest_bytes):
            raise FactoryError(
                "refusing to overwrite an immutable factory manifest: " + str(manifest_path)
            )
    else:
        manifest_path.write_bytes(manifest_bytes)
    return {
        "output": str(output),
        "scene": str(scene_path),
        "manifest": str(manifest_path),
        "sceneContentHash": manifest["scene"]["contentHash"],
        "assets": manifest["assets"],
    }


def authoring_request(plan, *, library_roots=None):
    """Describe the Blender workspace without importing Blender into the factory."""
    roots = [Path(item).resolve() for item in (library_roots or [BUILTIN_LIBRARY])]
    trusted = _library_assets(plan, roots)
    instances = []
    for instance in plan.instances:
        entry = {
            "instanceId": instance.instanceId,
            "entityKey": instance.entityKey,
            "asset": instance.asset,
            "positionM": instance.transform.positionM,
            "rotationDeg": instance.transform.rotationDeg,
            "seed": 0,
        }
        if instance.asset in trusted:
            entry |= {
                "kind": "trusted",
                "stagePath": str(Path(trusted[instance.asset]["path"]) / trusted[instance.asset]["stage"]),
            }
        else:
            spec = next(item for item in plan.procedural if item.ref == instance.asset)
            entry |= {"kind": "procedural", "seed": spec.seed}
            if spec.type == "six-hole-tube-rack":
                geometry = rack_geometry(spec.rack.model_dump())
                mesh = rack_mesh_json(spec.rack.model_dump())
                entry |= {
                    "type": spec.type,
                    "dimensionsM": list(geometry.dimensions_m),
                    "localBoundsM": _mesh_bounds(mesh),
                    "holeCount": len(geometry.sites),
                    "collision": spec.rack.collision,
                    "visualMesh": mesh,
                }
            else:
                geometry = tube_geometry(spec.tube.model_dump())
                mesh = tube_mesh_json(spec.tube.model_dump())
                entry |= {
                    "type": spec.type,
                    "dimensionsM": list(geometry.dimensions_m),
                    "localBoundsM": _mesh_bounds(mesh),
                    "visualMesh": mesh,
                }
            entry["visualMeshSha256"] = hashlib.sha256(
                json.dumps(entry["visualMesh"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            entry["meshSha256"] = _mesh_sha256(entry["visualMesh"])
        instances.append(entry)
    return {
        "schema": REQUEST_SCHEMA,
        "recipe": RECIPE,
        "planSha256": plan_digest(plan),
        "scene": {"name": plan.scene.name, "title": plan.scene.title},
        "libraryRoot": str(roots[0]),
        "libraryRoots": [str(root) for root in roots],
        "instances": instances,
        "dressing": [
            {
                "name": item.name,
                "sizeM": item.sizeM,
                "positionM": item.positionM,
                "color": item.color,
            }
            for item in plan.scene.dressing
        ],
    }


def request_digest(request):
    payload = deepcopy(request)
    payload.pop("requestSha256", None)
    payload.pop("libraryRoots", None)
    for instance in payload.get("instances") or []:
        if isinstance(instance, dict):
            instance.pop("meshSha256", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mesh_bounds(mesh):
    vertices = mesh["vertices"]
    return [
        [min(vertex[axis] for vertex in vertices) for axis in range(3)],
        [max(vertex[axis] for vertex in vertices) for axis in range(3)],
    ]


def _clean_number(value):
    number = round(float(value), 6)
    return 0.0 if number == 0 else number


def _mesh_sha256(mesh):
    # Vertex order is part of the identity: the faces index into it.
    vertices = [
        [_clean_number(vertex[0]), _clean_number(vertex[1]), _clean_number(vertex[2])]
        for vertex in mesh["vertices"]
    ]
    faces = [[int(index) for index in face] for face in mesh["faces"]]
    payload = json.dumps(
        {"vertices": vertices, "faces": faces}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
