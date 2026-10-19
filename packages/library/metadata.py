"""Asset and scene authoring metadata; indexing does not load a physics engine."""

import math
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from packages.info.declarations import AlertRules
from packages.library.archive import invalid, package_entries, relative_path
from packages.library.common import KINDS, NAME, LibraryError, document
from packages.sim_runtime.scenes import SCENE_CONFIGS

PYTHON_SOURCES = {"lab-demo": ("synthetic",), **{name: ("newton",) for name in SCENE_CONFIGS}}


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Physics(Schema):
    massKg: float | None = Field(default=None, gt=0)
    massSource: Literal["measured", "datasheet", "estimated"] | None = None
    collision: Literal["auto", "authored"] | None = None


class BindingTemplate(Schema):
    type: str = Field(min_length=1)
    programs: dict[str, str] = Field(default_factory=dict)
    config: dict[str, JsonValue] = Field(default_factory=dict)


class AssetMetadata(Schema):
    id: str
    kind: str
    title: str = Field(min_length=1)
    description: str = ""
    stage: str
    visual: str | None = None
    dimensionsM: list[float] | None = None
    physics: Physics | None = None
    implements: list[BindingTemplate] = Field(default_factory=list)
    dressing: bool = False

    @model_validator(mode="after")
    def package_identity(self):
        kind, separator, name = self.id.partition("/")
        if not separator or kind not in KINDS or kind != self.kind or not NAME.fullmatch(name):
            raise ValueError("id must be <kind>/<lowercase-name>, without a revision; kind must agree")
        if not self.title.strip():
            raise ValueError("title cannot be blank")
        if relative_path(self.stage).suffix not in (".usd", ".usda", ".usdc"):
            raise ValueError("stage must name a package-local USD file")
        if self.visual is not None and relative_path(self.visual).suffix != ".glb":
            raise ValueError("visual must name a package-local GLB file")
        if self.dimensionsM is not None and (
            len(self.dimensionsM) != 3
            or any(not math.isfinite(value) or value <= 0 for value in self.dimensionsM)
        ):
            raise ValueError("dimensionsM must contain three positive finite numbers")
        return self


def asset_metadata(path):
    files = package_entries(path, "asset.yaml")
    try:
        if (path / "asset.yaml").stat().st_size > 1024 * 1024:
            raise ValueError("asset.yaml exceeds 1 MiB")
        data = document(path / "asset.yaml")
        value = AssetMetadata.model_validate(data)
    except ValidationError as exc:
        raise LibraryError(
            "import_invalid",
            "Invalid asset metadata",
            422,
            problems=[
                {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                for error in exc.errors(include_input=False, include_url=False)
            ],
        ) from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise invalid("asset.yaml must be a valid metadata mapping", path="asset.yaml") from exc
    for field in ("stage", "visual"):
        filename = getattr(value, field)
        if filename is not None and filename not in files:
            raise invalid(
                "Referenced file is missing or its path case does not match the package", path=field
            )
    return data


class Simulation(Schema):
    solver: Literal["xpbd", "mujoco", "featherstone"] | None = None
    warmupSteps: int | None = Field(default=None, gt=0)


class SceneMetadata(Schema):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1)
    description: str = ""
    sources: list[Literal["newton", "synthetic"]] = Field(min_length=1)
    stage: str
    simulation: Simulation | None = None
    derivedFrom: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def declared_source(self):
        if not self.title.strip() or len(self.sources) != len(set(self.sources)):
            raise ValueError("Require a nonblank title and distinct sources")
        if self.stage.startswith("python:"):
            builder = self.stage.removeprefix("python:")
            if builder not in PYTHON_SOURCES:
                raise ValueError("Unknown registered Python builder")
            if any(source not in PYTHON_SOURCES[builder] for source in self.sources):
                raise ValueError("Python builder/source combination is not registered")
        elif relative_path(self.stage).suffix not in (".usd", ".usda", ".usdc") or self.sources != ["newton"]:
            raise ValueError("USD stages require a package-local USD file and sources: [newton]")
        if self.simulation is not None and "newton" not in self.sources:
            raise ValueError("Newton simulation settings require a Newton source")
        return self


class EntityBinding(Schema):
    key: str | None = None
    match: str | None = None
    type: str | None = None
    program: str | None = None
    template: str | None = None
    config: dict[str, JsonValue] = Field(default_factory=dict)
    interlocks: dict[str, list[dict[str, JsonValue]]] = Field(default_factory=dict)
    unplaced: bool = False
    alerts: AlertRules = Field(default_factory=dict)

    @model_validator(mode="after")
    def selectors(self):
        if (self.key is None) == (self.match is None):
            raise ValueError("Binding requires exactly one key or match")
        if self.template is None and (not self.type or not self.program):
            raise ValueError("Binding requires type/program or an asset template")
        return self


class SceneBindings(Schema):
    scene: str
    entities: list[EntityBinding]


class Location(Schema):
    site: str | None = None
    zone: str | None = None
    p: list[float] | None = Field(default=None, min_length=3, max_length=3)
    approach: list[float] | None = None
    grasp: list[float] | None = None
    requires: list[dict[str, JsonValue]] = Field(default_factory=list)


class Locations(Schema):
    robot: str | None = None
    locations: dict[str, Location]


def scene_metadata(path):
    files = package_entries(path, "scene.yaml")
    values = []
    for filename, schema in (
        ("scene.yaml", SceneMetadata),
        ("devices.yaml", SceneBindings),
        ("locations.yaml", Locations),
    ):
        if filename not in files:
            raise invalid("Required metadata file is missing or has different path case", path=filename)
        try:
            if (path / filename).stat().st_size > 1024 * 1024:
                raise ValueError("Metadata exceeds 1 MiB")
            value = document(path / filename)
            schema.model_validate(value)
        except ValidationError as exc:
            raise LibraryError(
                "import_invalid",
                "Invalid scene metadata",
                422,
                problems=[
                    {"path": filename + "." + ".".join(map(str, error["loc"])), "message": error["msg"]}
                    for error in exc.errors(include_input=False, include_url=False)
                ],
            ) from exc
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise invalid("Expected a valid metadata mapping", path=filename) from exc
        values.append(value)
    data, bindings, locations = values
    if bindings["scene"] != data["name"]:
        raise invalid("Binding document names another scene", path="devices.yaml.scene")
    if not data["stage"].startswith("python:") and data["stage"] not in files:
        raise invalid("Referenced USD stage is missing or has different path case", path="stage")
    return data, bindings, locations
