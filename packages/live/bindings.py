"""Typed nonspatial bindings compiled through the ordinary information Catalog."""

from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import Field, field_validator, model_validator

from packages.info.catalog import DEVICE_ROOT, compile_catalog
from packages.info.declarations import EntityBindingDeclaration
from packages.programs.biosafety_cabinet.mqtt import BiosafetyCabinetProgram
from packages.programs.env_sensor.mqtt import EnvSensorProgram
from packages.programs.incubator.mqtt import IncubatorProgram
from packages.protocol.schema import Model, NonEmpty

REGISTRY = {program.id: program for program in (
    IncubatorProgram, BiosafetyCabinetProgram, EnvSensorProgram,
)}


class Broker(Model):
    url: NonEmpty

    @field_validator("url")
    @classmethod
    def mqtt_url(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme not in ("mqtt", "mqtts") or not parsed.hostname
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Broker requires an mqtt:// or mqtts:// host URL")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Broker port must be in 1..65535")
        return value


class LiveEntity(EntityBindingDeclaration):
    placeAt: NonEmpty | None = None

    @field_validator("key")
    @classmethod
    def real_identity(cls, value):
        if not value.startswith("/Real/") or value == "/Real/":
            raise ValueError("Live entity keys must be under /Real/")
        return value

    @field_validator("placeAt")
    @classmethod
    def spatial_place(cls, value):
        if value is not None and not value.startswith("/World/"):
            raise ValueError("placeAt must identify a /World/ layout entity")
        return value


class LiveBindings(Model):
    live: NonEmpty
    broker: Broker
    layout: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")
    entities: list[LiveEntity] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_entities(self):
        keys = [entity.key for entity in self.entities]
        if len(keys) != len(set(keys)):
            raise ValueError("Live entity keys must be unique")
        if self.layout is None and any(entity.placeAt is not None for entity in self.entities):
            raise ValueError("placeAt requires a pinned layout scene version")
        return self


def load_bindings(path):
    return LiveBindings.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def live_catalog(bindings, revision, *, root=DEVICE_ROOT):
    """Keep layout metadata outside the published, unchanged Catalog schema."""
    types = {}
    for path in sorted((Path(root) / "types").glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if document["type"] in types:
            raise ValueError("Duplicate entity type")
        types[document["type"]] = document
    entities, configs, command_owners = {}, {}, {}
    for entity in bindings.entities:
        program = REGISTRY.get(entity.program)
        if program is None or program.type != entity.type:
            raise ValueError("Live binding requires a matching registered MQTT program/type")
        definition = types.get(entity.type)
        if definition is None:
            raise ValueError("Unknown live entity type")
        config = program.Config.model_validate(entity.config)
        if not set(config.telemetry.map) <= definition["properties"].keys():
            raise ValueError("Telemetry maps an undeclared device property")
        if not set(config.commands) <= definition["actions"].keys():
            raise ValueError("MQTT command maps an undeclared action")
        for command in config.commands.values():
            owner = command_owners.setdefault(command.topic, entity.key)
            if owner != entity.key:
                raise ValueError(
                    f"MQTT command topic {command.topic!r} is shared by {owner!r} and {entity.key!r}"
                )
        entities[entity.key] = entity.model_dump(mode="json", exclude={"placeAt"}, exclude_none=True)
        entities[entity.key]["config"] = config.model_dump(mode="json")
        configs[entity.key] = config
    return compile_catalog(revision, {entity.type: types[entity.type] for entity in bindings.entities},
                           entities, configs)
