"""Declarative script-package metadata; parsing never imports uploaded Python."""

import keyword
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from packages.library.archive import relative_path

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Name = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
Text = Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]


class Schema(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, allow_inf_nan=False, serialize_by_alias=True
    )


class ProgramIntent(Schema):
    id: str = Field(pattern=r"^program/robot_arm\.script\.[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")
    contentHash: Hash


class ProgramMetadata(Schema):
    schema_version: Literal["lw.program-package/1"] = Field(alias="schema")
    name: Name
    title: Text
    entrypoint: Text
    implements: str = Field(pattern=r"^robot-arm/v1\.[A-Za-z][A-Za-z0-9]*$")
    configSchema: dict[str, JsonValue] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    config: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("entrypoint")
    @classmethod
    def python_class(cls, value):
        path, separator, name = value.partition(":")
        if (
            not separator
            or relative_path(path).suffix != ".py"
            or not name.isidentifier()
            or keyword.iskeyword(name)
            or name.startswith("_")
        ):
            raise ValueError("entrypoint must identify a relative Python file and public top-level class")
        return value
