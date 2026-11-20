"""Versioned declarations consumed by the robot importer."""

from typing import Literal

from pydantic import Field

from packages.library.metadata import BindingTemplate, Schema


class JointRecipe(Schema):
    sign: Literal[-1, 1] | None = None
    zeroOffset: float = 0.
    defaultPosition: float | None = None
    kp: float | None = Field(default=None, ge=0)
    kv: float | None = Field(default=None, ge=0)
    armature: float | None = Field(default=None, ge=0)
    maxForce: float | None = Field(default=None, gt=0)


class SiteRecipe(Schema):
    body: str
    positionM: tuple[float, float, float]
    rpyDegrees: tuple[float, float, float] = (0., 0., 0.)


class Recipe(Schema):
    upAxis: Literal["Y", "Z"] | None = None
    rpyDegrees: tuple[float, float, float] = (0., 0., 0.)
    translationM: tuple[float, float, float] = (0., 0., 0.)
    metersPerUnit: float | None = Field(default=None, gt=0)
    scale: float = Field(default=1., gt=0, le=1000)
    density: float = Field(default=1000., gt=0)
    friction: float | None = Field(default=None, ge=0)
    fixedBase: bool | None = None
    standingKey: str | None = None
    joints: dict[str, JointRecipe] = Field(default_factory=dict, max_length=64)
    excludePairs: list[tuple[str, str]] = Field(default_factory=list, max_length=8192)
    sites: dict[str, SiteRecipe] = Field(default_factory=dict, max_length=256)
    implements: list[BindingTemplate] = Field(default_factory=list, max_length=32)


class Manifest(Schema):
    schema_version: Literal["lw.robot-import/1"] = Field(default="lw.robot-import/1", alias="schema")
    id: str = Field(pattern=r"^robot/[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=128)
    title: str = Field(min_length=1, max_length=256)
    entry: str = Field(max_length=512)
    license: str = Field(max_length=512)
    licenseId: str = Field(min_length=1, max_length=128)
    upstream: str | None = Field(default=None, max_length=1024)
    revision: str | None = Field(default=None, max_length=128)
    packages: dict[str, str] = Field(default_factory=dict, max_length=64)
    calibration: Recipe = Field(default_factory=Recipe)
