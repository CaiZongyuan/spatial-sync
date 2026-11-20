"""Content-addressed layouts published through the ordinary Scene import transaction."""

import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

import yaml
from pydantic import Field, model_validator

from packages.library.common import LibraryError, content_hash
from packages.library.metadata import EntityBinding, Locations, Schema, Simulation, Waypoint
from packages.library.transactions import durable_json

PrimName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
Coordinate = Annotated[float, Field(ge=-1000, le=1000)]
Length = Annotated[float, Field(gt=0, le=100)]
Position = Annotated[list[Coordinate], Field(min_length=3, max_length=3)]
Orientation = Annotated[list[float], Field(min_length=4, max_length=4)]


class Placement(Schema):
    name: PrimName
    position: Position = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    orientation: Orientation = Field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])

    @model_validator(mode="after")
    def rotation(self):
        if abs(math.hypot(*self.orientation) - 1) > 1e-6:
            raise ValueError("orientation requires a unit xyzw quaternion")
        return self


class Primitive(Placement):
    shape: Literal["plane", "box", "sphere"]
    size: list[Length] | None = Field(default=None, min_length=3, max_length=3)
    radius: Length | None = None
    mass: float | None = Field(default=None, gt=0, le=1e5)
    friction: float = Field(default=1.0, ge=0, le=10)

    @model_validator(mode="after")
    def geometry(self):
        if (self.size is not None) != (self.shape == "box") or (self.radius is not None) != (
            self.shape == "sphere"
        ):
            raise ValueError("box requires full size; sphere requires radius")
        if self.shape == "plane" and self.mass is not None:
            raise ValueError("A plane cannot be dynamic")
        return self


class AssetPlacement(Placement):
    asset: str = Field(pattern=r"^[a-z]+/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")
    contentHash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SceneDraft(Schema):
    schema_version: Literal["lw.scene-draft/1"] = Field(alias="schema")
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    simulation: Simulation | None = None
    assets: list[AssetPlacement] = Field(default_factory=list, max_length=64)
    terrain: list[Primitive] = Field(default_factory=list, max_length=256)
    objects: list[Primitive] = Field(default_factory=list, max_length=128)
    sites: list[Placement] = Field(default_factory=list, max_length=128)
    bindings: list[EntityBinding] = Field(default_factory=list, max_length=64)
    locations: Locations = Field(default_factory=Locations)
    waypoints: dict[PrimName, Waypoint] = Field(default_factory=dict, max_length=128)

    @model_validator(mode="after")
    def layout(self):
        for group in ((*self.assets, *self.objects), self.terrain, self.sites):
            names = {item.name for item in group}
            if len(names) != len(group):
                raise ValueError("Placement names must be unique")
        if {"Terrain", "Sites", "Waypoints"} & {item.name for item in (*self.assets, *self.objects)}:
            raise ValueError("Terrain, Sites and Waypoints are reserved prim groups")
        if any(item.mass is not None for item in self.terrain):
            raise ValueError("Terrain requires static colliders")
        if sum(item.shape == "plane" for item in (*self.terrain, *self.objects)) > 1:
            raise ValueError("A layout has at most one infinite plane")
        if self.locations.waypoints:
            raise ValueError("Use the top-level waypoints field")
        if any(not item.key or not item.key.startswith("/World/Lab/") for item in self.bindings):
            raise ValueError("Scene Draft bindings require exact canonical keys")
        return self


def _pose(prim, position, orientation):
    from pxr import Gf, UsdGeom

    xform = UsdGeom.Xformable(prim)
    xform.AddTranslateOp().Set(Gf.Vec3d(*position))
    x, y, z, w = orientation
    xform.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Quatd(w, Gf.Vec3d(x, y, z)))


def compose(stage_path, draft, assets):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

    stage = Usd.Stage.CreateNew(str(stage_path))
    world = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    stage.SetDefaultPrim(world)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/World/Lab").GetPrim()
    root.SetCustomDataByKey("lwSceneDraft", 1)
    for group in ("Terrain", "Sites", "Waypoints"):
        UsdGeom.Scope.Define(stage, f"/World/Lab/{group}")

    def primitive(item, parent):
        path = f"{parent}/{item.name}"
        body_path = path
        if item.mass is not None:
            body = UsdGeom.Xform.Define(stage, path).GetPrim()
            _pose(body, item.position, item.orientation)
            UsdPhysics.RigidBodyAPI.Apply(body)
            UsdPhysics.MassAPI.Apply(body).CreateMassAttr(item.mass)
            path += "/Collision"
        if item.shape == "plane":
            geom = UsdGeom.Plane.Define(stage, path)
        elif item.shape == "box":
            geom = UsdGeom.Cube.Define(stage, path)
            geom.CreateSizeAttr(2.0)
        else:
            geom = UsdGeom.Sphere.Define(stage, path)
            geom.CreateRadiusAttr(item.radius)
        prim = geom.GetPrim()
        if path == body_path:
            _pose(prim, item.position, item.orientation)
        if item.size:
            geom.AddScaleOp().Set(Gf.Vec3d(*(size / 2 for size in item.size)))
        UsdPhysics.CollisionAPI.Apply(prim)
        material = UsdShade.Material.Define(stage, path + "/Material")
        contact = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        contact.CreateStaticFrictionAttr(item.friction)
        contact.CreateDynamicFrictionAttr(item.friction)
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, materialPurpose="physics")

    for item in sorted(draft.terrain, key=lambda entry: entry.name):
        primitive(item, "/World/Lab/Terrain")
    for item in sorted(draft.objects, key=lambda entry: entry.name):
        primitive(item, "/World/Lab")
    for item in sorted(draft.assets, key=lambda entry: entry.name):
        asset = assets.resolve(item.asset)
        if asset.content_hash != item.contentHash or content_hash(asset.path) != item.contentHash:
            raise LibraryError("scene_draft_asset_changed", f"Pinned Asset differs: {item.asset}", 409)
        group = "/World/Dressing" if asset.metadata.get("dressing") else "/World/Lab"
        if not stage.GetPrimAtPath(group):
            UsdGeom.Xform.Define(stage, group)
        prim = UsdGeom.Xform.Define(stage, f"{group}/{item.name}").GetPrim()
        kind, name, revision = item.asset.replace("@", "/").split("/")
        ref = f"library:assets/{kind}/{name}/{revision}/{asset.metadata['stage']}"
        prim.GetReferences().AddReference(ref, Sdf.Path("/Asset"))
        _pose(prim, item.position, item.orientation)
    for item in sorted(draft.sites, key=lambda entry: entry.name):
        site = UsdGeom.Sphere.Define(stage, f"/World/Lab/Sites/{item.name}")
        site.CreateRadiusAttr(0.005)
        site.CreatePurposeAttr(UsdGeom.Tokens.guide)
        prim = site.GetPrim()
        prim.AddAppliedSchema("NewtonSiteAPI")
        _pose(prim, item.position, item.orientation)
    locations = draft.locations.model_dump(exclude_none=True)
    locations["waypoints"] = {}
    for key, waypoint in sorted(draft.waypoints.items()):
        path = f"/World/Lab/Waypoints/{key}"
        if waypoint.site and waypoint.site != path:
            raise LibraryError("scene_draft_invalid", f"Waypoint {key} site must be {path}", 422)
        prim = UsdGeom.Xform.Define(stage, path).GetPrim()
        _pose(prim, waypoint.p, (0.0, 0.0, math.sin(waypoint.yaw / 2), math.cos(waypoint.yaw / 2)))
        locations["waypoints"][key] = waypoint.model_dump(exclude_none=True) | {"site": path}
    stage.GetRootLayer().Save()
    return locations


def draft_id(document):
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 1024 * 1024:
        raise LibraryError("scene_draft_invalid", "Scene Draft exceeds 1 MiB", 422)
    return "draft-" + hashlib.sha256(encoded).hexdigest()


class SceneDrafts:
    def __init__(self, state_root, importer):
        self.root = Path(state_root) / "scene-drafts"
        self.importer = importer

    def submit(self, draft):
        document = draft.model_dump(mode="json", by_alias=True, exclude_none=True)
        identity = draft_id(document)
        durable_json(self.root / (identity + ".json"), document)
        return {"id": identity, "draft": document}

    def show(self, identity):
        path = self.root / (identity + ".json")
        if not path.is_file():
            raise LibraryError("unknown_scene_draft", "Unknown Scene Draft", 404)
        document = json.loads(path.read_bytes())
        if draft_id(document) != identity:
            raise LibraryError("scene_draft_changed", "Scene Draft identity changed", 409)
        return {"id": identity, "draft": document}

    def publish(self, identity):
        draft = SceneDraft.model_validate(self.show(identity)["draft"])
        with TemporaryDirectory(dir=self.root) as temporary:
            root = Path(temporary)
            locations = compose(root / "stage.usda", draft, self.importer.assets)
            scene = {
                "name": draft.name,
                "title": draft.title,
                "description": draft.description,
                "sources": ["newton"],
                "stage": "stage.usda",
                "simulation": (draft.simulation.model_dump(exclude_none=True) if draft.simulation else {}),
            }
            for file, data in (
                ("scene.yaml", scene),
                ("locations.yaml", locations),
                (
                    "devices.yaml",
                    {
                        "scene": draft.name,
                        "entities": [
                            item.model_dump(exclude_none=True)
                            for item in sorted(draft.bindings, key=lambda row: row.key)
                        ],
                    },
                ),
            ):
                (root / file).write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
            return self.importer.import_staged(root, allowed_root=self.root)
