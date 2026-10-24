"""Validated robot resource metadata; reads never start physics or model inference."""

import hashlib
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from packages.library.archive import destination_parent
from packages.library.common import NAME, LibraryError
from packages.library.templates import TemplateError
from packages.library.templates import resolve as resolve_templates

Name = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
PinnedScene = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")]


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Binding(Document):
    scene: PinnedScene
    entity: str = Field(pattern=r"^/[^\s]+$", max_length=1024)
    source: Literal["synthetic", "newton"]


class Embodiment(Document):
    id: Name
    title: str = Field(min_length=1, max_length=256)
    kind: Literal["sim", "real"]
    asset: str = Field(pattern=r"^robot/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")
    controller: Name | None
    bindings: list[Binding]

    @model_validator(mode="after")
    def real_is_unconnected(self):
        if self.kind == "real" and (self.bindings or self.controller is not None):
            raise ValueError("Real robot integration is unavailable")
        return self


class Controller(Document):
    id: Name
    title: str = Field(min_length=1, max_length=256)
    embodiment: Name
    currentVersion: int | None = Field(ge=1)


class Action(Document):
    space: str = Field(min_length=1, max_length=256)
    hz: float | None = Field(default=None, gt=0)


class ControllerVersion(Document):
    controller: Name
    version: int = Field(ge=1)
    embodiment: Name
    robotModel: Name
    policy: str | None
    program: str = Field(min_length=1, max_length=256)
    action: Action
    observation: str | None
    task: str | None
    scene: PinnedScene
    evaluationId: str | None


class Capability(Document):
    program: str | None
    sources: list[Literal["synthetic", "newton"]]
    requirements: list[str]
    notes: str


class Quality(Document):
    status: Literal["not-evaluated", "evaluated"]
    evaluationId: str | None


class Source(Document):
    title: str
    url: str = Field(pattern=r"^https://[^\s]+$")
    retrievedOn: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class Compute(Document):
    inference: str


class RobotModel(Document):
    id: Name
    title: str
    family: Literal["vla", "rl-policy", "scripted"]
    source: str
    license: str | None
    parameters: int | None = Field(ge=1)
    embodiments: list[Name]
    upstreamEmbodiments: list[str]
    actionSpace: str
    compute: Compute
    runnable: bool
    capability: Capability
    quality: Quality
    sources: list[Source] = Field(min_length=1)

    @model_validator(mode="after")
    def capability_is_explicit(self):
        if self.family == "vla" and (self.runnable or self.embodiments or self.capability.program):
            raise ValueError("VLA entries are registration-only")
        if self.runnable and (not self.capability.program or not self.capability.sources):
            raise ValueError("Runnable models need a supported platform Program")
        if (self.quality.status == "evaluated") != (self.quality.evaluationId is not None):
            raise ValueError("Quality requires an explicit evaluation")
        return self


class RobotResources:
    def __init__(self, roots, assets, scenes, programs):
        self.roots = {name: Path(root).resolve() for name, root in roots.items()}
        self.assets, self.scenes, self.programs = assets, scenes, programs
        self._version_hashes = {}

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not NAME.fullmatch(value):
            raise LibraryError("unknown_robot_resource", "Unknown robot resource")
        return value

    @staticmethod
    def _read(root, relative, schema, identifier):
        path = destination_parent(root, relative.parts[:-1]) / relative.name
        try:
            if (path.is_symlink() or path.is_junction() or not path.is_file()
                    or path.stat().st_nlink != 1 or path.stat().st_size > 256 * 1024):
                raise ValueError
            content = path.read_bytes()
            value = schema.model_validate(yaml.safe_load(content)).model_dump()
            if value.get("id", identifier) != identifier:
                raise ValueError
        except (OSError, ValueError, ValidationError, yaml.YAMLError):
            raise LibraryError("robot_resource_invalid", "Robot resource metadata is invalid", 409) from None
        return value | {"contentHash": hashlib.sha256(content).hexdigest()}

    def _documents(self, kind, schema):
        values = {}
        for origin, root in self.roots.items():
            directory = destination_parent(root, (kind,))
            files = directory.glob("*/controller.yaml" if kind == "controllers" else "*.yaml")
            for file in sorted(files):
                identifier = self._name(file.parent.name if kind == "controllers" else file.stem)
                record = self._read(root, file.relative_to(root), schema, identifier)
                values[identifier] = record | {"root": origin}
        return values

    def models(self):
        values = self._documents("robot-models", RobotModel)
        embodiments = self._documents("embodiments", Embodiment)
        for value in values.values():
            if any(name not in embodiments for name in value["embodiments"]):
                raise LibraryError("unknown_embodiment", "Robot Model references an unknown Embodiment", 409)
            if value["runnable"]:
                definition = self.programs.get(value["capability"]["program"])
                if definition is None or value["capability"]["sources"] != [definition["source"]]:
                    raise LibraryError("robot_program_unavailable", "Robot Model Program is unsupported", 409)
        return sorted(values.values(), key=lambda value: value["id"])

    def model(self, identifier):
        return self._find(self.models(), identifier)

    def _find(self, values, identifier):
        self._name(identifier)
        value = next((value for value in values if value["id"] == identifier), None)
        if value is None:
            raise LibraryError("unknown_robot_resource", "Unknown robot resource")
        return value

    def controller(self, identifier):
        metadata = self._find(list(self._documents("controllers", Controller).values()), identifier)
        embodiments = self._documents("embodiments", Embodiment)
        if metadata["embodiment"] not in embodiments:
            raise LibraryError("unknown_embodiment", "Controller references an unknown robot", 409)
        models = {value["id"]: value for value in self.models()}
        versions = {}
        for root in self.roots.values():
            directory = destination_parent(root, ("controllers", identifier))
            for file in sorted(directory.glob("*.yaml")):
                if file.name == "controller.yaml":
                    continue
                if not re.fullmatch(r"[1-9][0-9]*\.yaml", file.name):
                    raise LibraryError("robot_resource_invalid", "Invalid Controller version identity", 409)
                number = int(file.stem)
                value = self._read(root, file.relative_to(root), ControllerVersion, identifier)
                if value["controller"] != identifier or value["version"] != number:
                    raise LibraryError("robot_resource_invalid", "Controller version identity differs", 409)
                model = models.get(value["robotModel"])
                if (value["embodiment"] != metadata["embodiment"] or model is None
                        or value["embodiment"] not in model["embodiments"]
                        or model["capability"]["program"] != value["program"]):
                    raise LibraryError("controller_binding_mismatch", "Controller composition differs", 409)
                self.scenes.resolve(value["scene"])
                if number in versions and versions[number] != value:
                    raise LibraryError("controller_version_conflict", "Controller version conflicts", 409)
                pinned = self._version_hashes.setdefault((identifier, number), value["contentHash"])
                if pinned != value["contentHash"]:
                    raise LibraryError("controller_version_changed", "Controller snapshot changed", 409)
                versions[number] = value
        current = versions.get(metadata["currentVersion"])
        if metadata["currentVersion"] is not None and current is None:
            raise LibraryError("unknown_controller_version", "Current Controller version is unavailable", 409)
        return metadata | {"current": current, "versions": [versions[key] for key in sorted(versions)]}

    def embodiments(self):
        models = self.models()
        values = self._documents("embodiments", Embodiment)
        result = []
        for value in values.values():
            asset = self.assets.resolve(value["asset"])
            summary = asset.summary()
            bindings = []
            for binding in value["bindings"]:
                scene = self.scenes.resolve(binding["scene"], source=binding["source"])
                self.scenes.indexed_file(scene.root, f"scenes/{scene.name}/{scene.rev}/scene.yaml")
                try:
                    declarations = resolve_templates(scene.bindings, binding["source"], scene.template_assets)
                except TemplateError:
                    raise LibraryError(
                        "robot_binding_mismatch", "Robot Scene templates unavailable", 409
                    ) from None
                match = next((item for item in declarations.declarations
                              if item.binding.get("key") == binding["entity"]), None)
                authored = next((item for item in scene.bindings["entities"]
                                 if item.get("key") == binding["entity"]), None)
                if match is None or authored is None or authored.get("template") != value["asset"]:
                    raise LibraryError("robot_binding_mismatch", "Scene does not bind this robot asset", 409)
                program = match.binding["program"]
                model = next((item for item in models if item["capability"]["program"] == program), None)
                config = (match.template_config or {}) | match.binding.get("config", {})
                bindings.append(binding | {"sceneContentHash": scene.content_hash, "program": program,
                                           "robotModel": model["id"] if model else None,
                                           "graspMode": config.get("graspMode")})
            controller = self.controller(value["controller"]) if value["controller"] else None
            if controller and controller["embodiment"] != value["id"]:
                raise LibraryError("controller_embodiment_mismatch", "Controller robot identity differs", 409)
            result.append(value | {"bindings": bindings, "visualUrl": summary["visualUrl"],
                                   "assetContentHash": summary["contentHash"],
                                   "connection": "not-connected" if value["kind"] == "real" else "simulation",
                                   "currentController": controller["current"] if controller else None,
                                   "controllerHistory": controller["versions"] if controller else []})
        return sorted(result, key=lambda value: value["id"])

    def embodiment(self, identifier):
        return self._find(self.embodiments(), identifier)
