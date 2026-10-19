"""S1–S8 in a CPU-only worker. Composition only sees preflighted layer copies."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from copy import deepcopy
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from importlib import import_module
from importlib.metadata import version
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml
from pxr import Ar, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils
from pydantic import ValidationError

from packages.library.common import content_hash, document
from packages.library.metadata import Locations
from packages.library.reports import parse_report


def check(identifier, status, message, **details):
    return {"id": identifier, "tier": "structural", "status": status, "message": message, **details}


def entity_path(path, *, asset=False):
    """USD guarantees path syntax and uniqueness; the public identity additionally requires ASCII."""
    root = "/Asset" if asset else "/World/Lab"
    return path.isascii() and (path == root or path.startswith(root + "/"))


def config_locations(value, package, entities):
    if isinstance(value, str):
        # Exact indexed lookup rejects host paths, escapes, links and path-case aliases.
        if value not in package["files"]:
            raise ValueError("Config locations must name an indexed package-local document")
        path = Path(package["files"][value])
        if path.is_symlink() or not path.resolve().is_relative_to(Path(package["path"]).resolve()):
            raise ValueError("Config location document changed to an external path")
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Config location document exceeds 1 MiB")
        value = document(path)
    value = Locations.model_validate(value).model_dump(exclude_unset=True)
    if value.get("robot") is not None and value["robot"] not in entities:
        raise ValueError("Config location document belongs to another robot: " + value["robot"])
    return value


def checked_config(program, config, kind):
    if not program.get("configModule"):
        return  # Small SDK fixtures can supply role metadata without a runtime Config class.
    owner = import_module(program["configModule"])
    for part in program["configClass"].split("."):
        owner = getattr(owner, part)
    try:
        owner.Config.model_validate(config)
    except ValidationError as exc:
        # An asset template is partial: locations/tubes can be supplied by its eventual scene.
        errors = [
            error
            for error in exc.errors(include_input=False, include_url=False)
            if kind != "asset" or error["type"] != "missing"
        ]
        if errors:
            raise ValueError(
                "Invalid program Config: "
                + "; ".join(".".join(map(str, error["loc"])) + ": " + error["msg"] for error in errors)
            ) from exc


class USDGraph:
    """Preflight and rewrite all layer asset paths before opening a composed stage."""

    def __init__(self, package, assets):
        self.package = package
        self.assets = {asset["id"]: asset for asset in assets}
        self.library_files = {
            f"assets/{asset['id'].replace('@', '/')}/{filename}": (asset, filename)
            for asset in assets
            for filename in asset["files"]
        }
        self.clones, self.errors, self.referenced = {}, [], set()
        self.verified = set()

    def resolve(self, owner, filename, reference):
        if not reference:
            return None
        if Ar.IsPackageRelativePath(reference):
            raise ValueError("Unsupported packaged USD member reference; upload unpacked USD layers")
        if reference.startswith("library:"):
            target = self.library_files.get(reference.removeprefix("library:"))
            if target is None:
                raise ValueError(f"Unknown indexed library reference: {reference}")
            self.referenced.add(target[0]["id"])
            return target
        if PureWindowsPath(reference).drive or "\\" in reference or ":" in reference:
            raise ValueError("External host paths and URI references are not allowed")
        relative = PurePosixPath(reference)
        if relative.is_absolute():
            raise ValueError("Absolute USD references are not allowed")
        parts = list(PurePosixPath(filename).parent.parts)
        for part in relative.parts:
            if part == "..":
                if not parts:
                    raise ValueError("USD dependency escapes its package")
                parts.pop()
            elif part != ".":
                parts.append(part)
        target = "/".join(parts)
        if target not in owner["files"]:
            raise ValueError(f"Package dependency is missing or has different path case: {target}")
        return owner, target

    def layer(self, owner, filename):
        actual = Path(owner["files"][filename])
        if actual.is_symlink() or not actual.resolve().is_relative_to(Path(owner["path"]).resolve()):
            raise ValueError("Indexed USD dependency changed to an external path")
        if owner is not self.package and owner["id"] not in self.verified:
            if content_hash(Path(owner["path"])) != owner["contentHash"]:
                raise ValueError("Referenced immutable asset changed")
            self.verified.add(owner["id"])
        key = str(actual)
        if key in self.clones:
            return self.clones[key]
        if len(self.clones) >= 4096:
            raise ValueError("USD dependency count exceeds the package budget")
        original = Sdf.Layer.FindOrOpen(key)
        if original is None:
            raise ValueError(f"USD layer cannot be read: {filename}")
        clone = Sdf.Layer.CreateAnonymous(".usda")
        clone.TransferContent(original)
        self.clones[key] = clone
        composition_references = set(original.GetExternalReferences())

        def confined(reference):
            try:
                resolved = self.resolve(owner, filename, reference)
                if resolved is None:
                    return reference
                target_owner, target = resolved
                suffix = PurePosixPath(target).suffix.lower()
                if suffix == ".usdz" or (
                    reference in composition_references and suffix not in (".usd", ".usda", ".usdc")
                ):
                    raise ValueError("Unsupported USD layer format; upload unpacked usd/usda/usdc layers")
                if reference in composition_references:
                    return self.layer(target_owner, target).identifier
                return target_owner["files"][target]
            except (ValueError, OSError) as exc:
                self.errors.append(str(exc))
                return ""

        # This includes references, payloads, sublayers and other asset-valued properties.
        UsdUtils.ModifyAssetPaths(clone, confined)
        return clone

    def compose(self, stage_file):
        layer = self.layer(self.package, stage_file)
        if self.errors:
            return None
        stage = Usd.Stage.Open(layer)
        if stage is None:
            raise ValueError("USD stage cannot be composed")
        if stage.GetCompositionErrors():
            self.errors.append("USD references or payloads cannot be composed")
        return stage


def image_dimensions(value):
    if value.startswith(b"\x89PNG\r\n\x1a\n") and len(value) >= 24:
        return struct.unpack(">II", value[16:24])
    if value.startswith(b"\xff\xd8"):
        cursor = 2
        while cursor + 4 <= len(value):
            if value[cursor] != 255:
                cursor += 1
                continue
            marker = value[cursor + 1]
            cursor += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = int.from_bytes(value[cursor : cursor + 2], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3) and length >= 7:
                return (
                    int.from_bytes(value[cursor + 5 : cursor + 7], "big"),
                    int.from_bytes(value[cursor + 3 : cursor + 5], "big"),
                )
            if length < 2:
                break
            cursor += length
    if len(value) >= 30 and value[:4] == b"RIFF" and value[8:16] == b"WEBPVP8X":
        return (1 + int.from_bytes(value[24:27], "little"), 1 + int.from_bytes(value[27:30], "little"))
    raise ValueError("Unsupported or malformed embedded texture")


def glb_budget(path):
    try:
        return inspect_glb(path)
    except (KeyError, IndexError, TypeError, AttributeError, struct.error) as exc:
        raise ValueError("Malformed GLB document, buffer, or accessor") from exc


def inspect_glb(path):
    value = Path(path).read_bytes()
    if len(value) < 20 or value[:4] != b"glTF" or struct.unpack_from("<I", value, 4)[0] != 2:
        raise ValueError("Visual is not a glTF2 binary")
    if struct.unpack_from("<I", value, 8)[0] != len(value):
        raise ValueError("GLB length does not match its bytes")
    chunks, cursor = {}, 12
    while cursor + 8 <= len(value):
        length, kind = struct.unpack_from("<II", value, cursor)
        cursor += 8
        if length % 4 or cursor + length > len(value) or kind in chunks:
            raise ValueError("GLB chunk exceeds its bytes")
        chunks[kind] = value[cursor : cursor + length]
        cursor += length
    if cursor != len(value):
        raise ValueError("GLB has trailing or incomplete chunks")
    document = json.loads(chunks[0x4E4F534A])
    if document["asset"]["version"] != "2.0":
        raise ValueError("Visual is not a glTF2 document")
    binary = chunks.get(0x004E4942, b"")
    buffers = document.get("buffers", [])
    if len(buffers) != 1 or "uri" in buffers[0] or not 0 <= buffers[0]["byteLength"] <= len(binary):
        raise ValueError("GLB must contain one bounded embedded binary buffer")
    views = document.get("bufferViews", [])
    for view in views:
        start, length = view.get("byteOffset", 0), view["byteLength"]
        if (
            view["buffer"] != 0
            or type(start) is not int
            or type(length) is not int
            or start < 0
            or length < 0
            or start + length > buffers[0]["byteLength"]
        ):
            raise ValueError("GLB bufferView exceeds its embedded buffer")

    def accessor_count(index, *, positions=False):
        accessor = document["accessors"][index]
        count, offset = accessor["count"], accessor.get("byteOffset", 0)
        components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
        widths = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
        width = widths[accessor["componentType"]] * components[accessor["type"]]
        if type(count) is not int or count <= 0 or type(offset) is not int or offset < 0:
            raise ValueError("GLB accessor has invalid count or offset")
        if positions and (accessor["type"] != "VEC3" or accessor["componentType"] != 5126):
            raise ValueError("GLB POSITION must use floating point VEC3 coordinates")
        if "bufferView" in accessor:
            view = views[accessor["bufferView"]]
            stride = view.get("byteStride", width)
            if (
                type(stride) is not int
                or stride < width
                or offset + (count - 1) * stride + width > view["byteLength"]
            ):
                raise ValueError("GLB accessor exceeds its bufferView")
        elif "sparse" not in accessor:
            raise ValueError("GLB accessor has neither embedded nor sparse data")
        if "sparse" in accessor:
            sparse = accessor["sparse"]
            if type(sparse["count"]) is not int or not 0 < sparse["count"] <= count:
                raise ValueError("GLB sparse accessor has invalid count")
            for field, item_width in (
                ("indices", widths[sparse["indices"]["componentType"]]),
                ("values", width),
            ):
                item = sparse[field]
                if item.get("byteOffset", 0) < 0 or (
                    item.get("byteOffset", 0) + sparse["count"] * item_width
                    > views[item["bufferView"]]["byteLength"]
                ):
                    raise ValueError("GLB sparse accessor exceeds its bufferView")
        return count

    triangles = 0
    for mesh in document.get("meshes", []):
        for primitive in mesh["primitives"]:
            count = accessor_count(primitive["attributes"]["POSITION"], positions=True)
            if "indices" in primitive:
                count = accessor_count(primitive["indices"])
                accessor = document["accessors"][primitive["indices"]]
                if accessor["type"] != "SCALAR" or accessor["componentType"] not in (5121, 5123, 5125):
                    raise ValueError("GLB indices must be unsigned scalar coordinates")
            mode = primitive.get("mode", 4)
            if mode not in range(7) or (mode == 4 and count % 3):
                raise ValueError("GLB primitive mode or triangle coordinate count is invalid")
            triangles += count // 3 if mode == 4 else max(0, count - 2) if mode in (5, 6) else 0
    if triangles > 500000:
        raise ValueError(f"Visual has {triangles} triangles; maximum is 500000")
    for image in document.get("images", []):
        if "bufferView" not in image:
            raise ValueError("GLB textures must be embedded in the uploaded package")
        view = views[image["bufferView"]]
        start = view.get("byteOffset", 0)
        pixels = binary[start : start + view["byteLength"]]
        width, height = image_dimensions(pixels)
        if min(width, height) <= 0 or max(width, height) > 4096:
            raise ValueError(f"Texture is {width}x{height}; maximum is 4096x4096")
    return triangles


def validate(payload):
    kind, metadata, package = payload["kind"], payload["metadata"], payload["package"]
    checks = {"S1": check("S1", "pass", "Package metadata and declared files match their schemas")}
    graph = USDGraph(package, payload["assets"])
    stage, parse_error = None, None
    python_stage = metadata["stage"].startswith("python:")
    if not python_stage:
        try:
            stage = graph.compose(metadata["stage"])
        except Exception as exc:
            parse_error = str(exc)
    if graph.errors:
        checks["S7"] = check("S7", "fail", "; ".join(dict.fromkeys(graph.errors)))
    elif parse_error:
        checks["S7"] = check("S7", "fail", "USD layer or dependency cannot be read")
    else:
        checks["S7"] = check(
            "S7",
            "warn" if python_stage else "pass",
            "Python stages have no USD references"
            if python_stage
            else "USD dependencies resolve only to package-local or indexed library files",
            **({"notApplicable": True} if python_stage else {}),
        )
    prims = list(stage.Traverse()) if stage else []
    if stage:
        correct = UsdGeom.GetStageUpAxis(stage) == "Z" and UsdGeom.GetStageMetersPerUnit(stage) == 1
        checks["S2"] = check("S2", "pass" if correct else "fail", "Stage must use Z up and metersPerUnit=1")
    else:
        checks["S2"] = check(
            "S2",
            "warn",
            "USD composition was skipped or is unavailable",
            **({"notApplicable": True} if python_stage else {}),
        )
    bad_paths = []
    if kind == "scene" and stage:
        for prim in prims:
            path = str(prim.GetPath())
            physical = (
                prim.HasAPI(UsdPhysics.RigidBodyAPI)
                or prim.HasAPI(UsdPhysics.CollisionAPI)
                or prim.IsA(UsdPhysics.Joint)
            )
            if path.startswith("/World/Lab/") and not entity_path(path):
                bad_paths.append(path)
            if (
                (physical or prim.HasAuthoredReferences())
                and not entity_path(path)
                and not (path.startswith("/World/Dressing/") and not physical)
            ):
                bad_paths.append(path)
        checks["S3"] = check(
            "S3",
            "fail" if bad_paths else "pass",
            "Entity paths must be ASCII under /World/Lab and separate from dressing"
            + (": " + ", ".join(bad_paths) if bad_paths else ""),
        )
    elif kind == "asset" and stage:
        roots = [str(prim.GetPath()) for prim in stage.GetPseudoRoot().GetChildren()]
        correct_root = roots == ["/Asset"] and stage.GetDefaultPrim().GetPath() == Sdf.Path("/Asset")
        checks["S3"] = check(
            "S3", "pass" if correct_root else "fail", "Asset root and default prim must be /Asset"
        )
    else:
        checks["S3"] = check(
            "S3",
            "warn",
            "Entity namespace check does not apply to this stage",
            notApplicable=kind == "asset" or python_stage,
        )
    license_files = [
        name for name in package["files"] if name.casefold() in ("license", "license.txt", "license.md")
    ]
    provenance_problems = []
    if kind == "asset":
        if not license_files:
            provenance_problems.append("Original license file is missing")
        try:
            provenance = json.loads(Path(package["files"]["provenance.json"]).read_text(encoding="utf-8"))
            if not isinstance(provenance.get("files"), dict) or not provenance["files"]:
                raise ValueError("Missing source file hashes")
            for name, expected in provenance["files"].items():
                actual = Path(package["files"][name]).read_bytes()
                if hashlib.sha256(actual).hexdigest() != expected["sha256"] or (
                    "bytes" in expected and expected["bytes"] != len(actual)
                ):
                    provenance_problems.append(f"Source hash differs: {name}")
        except (KeyError, ValueError, OSError, TypeError):
            provenance_problems.append(
                "provenance.json or its package-local file hashes are missing or invalid"
            )
        checks["S4"] = check(
            "S4",
            "fail" if provenance_problems else "pass",
            "; ".join(provenance_problems) or "Original license and declared file hashes exist and match",
        )
    else:
        checks["S4"] = check(
            "S4", "warn", "Asset provenance check applies to asset versions", notApplicable=True
        )
    bindings = (
        payload.get("bindings", {}).get("entities", []) if kind == "scene" else metadata.get("implements", [])
    )
    reference_problems, posture_groups, bound = [], [], set()
    types = payload["types"]
    for binding in bindings:
        entity = binding.get("key", "/Asset") if kind == "scene" else "/Asset"
        entities = [entity]
        if kind == "scene":
            selector = binding.get("match") or entity
            if not isinstance(selector, str) or not entity_path(selector):
                reference_problems.append("Binding selector must use an ASCII /World/Lab entity path")
            if binding.get("match"):
                entities = sorted(
                    str(prim.GetPath()) for prim in prims if fnmatchcase(str(prim.GetPath()), selector)
                )
                if stage is not None and not entities:
                    reference_problems.append("Binding selector matched no entities: " + selector)
            elif stage is not None and not stage.GetPrimAtPath(entity) and not binding.get("unplaced"):
                reference_problems.append("Binding entity does not exist: " + str(entity))
            for key in entities:
                if key in bound:
                    reference_problems.append("Duplicate binding: " + key)
                bound.add(key)
        if binding.get("template"):
            if binding["template"] not in graph.assets:
                reference_problems.append("Unknown asset template: " + binding["template"])
            continue  # Template expansion is a separate capability; never invent its effective bindings.
        if binding.get("type") not in types:
            reference_problems.append("Unknown entity type: " + str(binding.get("type")))
        programs = binding.get("programs", {}) if kind == "asset" else {"declared": binding.get("program")}
        for program_id in programs.values():
            program = payload["programs"].get(program_id)
            if program is None or program["type"] != binding.get("type"):
                reference_problems.append("Unknown program or program/type mismatch: " + str(program_id))
                continue
            source = program.get("source")
            if source is not None and (
                (kind == "asset" and programs.get(source) != program_id)
                or (kind == "scene" and source not in metadata["sources"])
            ):
                reference_problems.append(
                    "Program is unavailable for the declared source: " + str(program_id)
                )
            config = deepcopy(binding.get("config", {}))
            for field, information in program["fields"].items():
                if information.get("documentSchema") == "locations" and field in config:
                    try:
                        config[field] = config_locations(config[field], package, entities)
                    except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
                        reference_problems.append(str(exc))
            try:
                checked_config(program, config, kind)
            except ValueError as exc:
                reference_problems.append(str(exc))
            for field, information in program["fields"].items():
                role = information.get("spatialReference")
                if not role or field not in config:
                    continue
                values = config[field] if isinstance(config[field], list) else [config[field]]
                paths = []
                for value in values:
                    path = (
                        "/Asset/" + value
                        if kind == "asset" and isinstance(value, str) and not value.startswith("/")
                        else value
                    )
                    if not isinstance(path, str):
                        reference_problems.append(f"{field} requires a spatial path")
                        continue
                    matches = [
                        str(prim.GetPath()) for prim in prims if fnmatchcase(str(prim.GetPath()), path)
                    ]
                    if not matches and stage is not None:
                        reference_problems.append(f"{field} references a missing {role}: {path}")
                    for target in matches:
                        prim = stage.GetPrimAtPath(target)
                        if role == "joint" and not prim.IsA(UsdPhysics.Joint):
                            reference_problems.append(f"{field} does not reference a joint: {target}")
                        elif role == "body" and not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                            reference_problems.append(f"{field} does not reference a rigid body: {target}")
                        elif role == "site" and (
                            not prim.IsA(UsdGeom.Xformable)
                            or prim.HasAPI(UsdPhysics.RigidBodyAPI)
                            or prim.HasAPI(UsdPhysics.CollisionAPI)
                        ):
                            reference_problems.append(
                                f"{field} does not reference a transform site: {target}"
                            )
                        paths.append(target)
                if information.get("posture"):
                    if len(paths) != len(set(paths)):
                        reference_problems.append(f"{field} contains duplicate joints")
                    taught = config.get("locations", payload.get("locations", {}))
                    if isinstance(taught, dict):
                        if taught.get("robot") is not None and taught["robot"] not in entities:
                            reference_problems.append(
                                "Location document belongs to another robot: " + taught["robot"]
                            )
                        else:
                            for robot in [taught["robot"]] if taught.get("robot") else entities:
                                posture_groups.append((robot, paths, taught.get("locations", {})))
    checks["S5"] = check(
        "S5",
        "fail" if reference_problems else "warn" if stage is None else "pass",
        "; ".join(dict.fromkeys(reference_problems))
        or (
            "Program/type declarations checked; stage resources were not inspected"
            if stage is None
            else "Declared types and Config spatial references exist"
        ),
    )
    visual_problem, triangles = None, None
    if kind == "asset" and metadata.get("visual"):
        try:
            triangles = glb_budget(package["files"][metadata["visual"]])
        except (KeyError, ValueError, TypeError, IndexError, struct.error) as exc:
            visual_problem = str(exc)
        checks["S6"] = check(
            "S6",
            "fail" if visual_problem else "pass",
            visual_problem or f"Visual budget: {triangles} triangles; embedded textures <=4096x4096",
        )
    elif kind == "asset":
        checks["S6"] = check("S6", "warn", "PBR visual is missing")
    else:
        dressing = [
            prim
            for prim in prims
            if str(prim.GetPath()).startswith("/World/Dressing/") and prim.HasAuthoredReferences()
        ]
        checks["S6"] = check(
            "S6",
            "warn" if stage is None else "fail" if len(dressing) > 64 else "pass",
            "Dressing visual budget was not inspected because the USD stage is unavailable"
            if stage is None
            else f"Dressing has {len(dressing)} referenced visual groups; maximum is 64",
        )
    posture_problems = []
    for entity, joints, locations in posture_groups if stage is not None else []:
        for name, location in locations.items():
            for field in ("approach", "grasp"):
                if field not in location:
                    continue
                values = location[field]
                if len(values) != len(joints):
                    posture_problems.append(
                        (
                            entity,
                            name,
                            f"{name}.{field} has {len(values)} coordinates; expected {len(joints)}",
                        )
                    )
                    continue
                for target, value in zip(joints, values):
                    prim = stage.GetPrimAtPath(target)
                    if prim.IsA(UsdPhysics.RevoluteJoint):
                        joint, factor = UsdPhysics.RevoluteJoint(prim), math.pi / 180
                    elif prim.IsA(UsdPhysics.PrismaticJoint):
                        joint, factor = UsdPhysics.PrismaticJoint(prim), 1
                    else:
                        posture_problems.append(
                            (entity, name, f"{name}.{field}: {target} has no scalar posture coordinate")
                        )
                        continue
                    lower, upper = joint.GetLowerLimitAttr().Get(), joint.GetUpperLimitAttr().Get()
                    if (
                        not isinstance(lower, (int, float))
                        or not isinstance(upper, (int, float))
                        or isinstance(lower, bool)
                        or isinstance(upper, bool)
                        or math.isnan(lower)
                        or math.isnan(upper)
                        or lower > upper
                    ):
                        posture_problems.append(
                            (entity, name, f"{name}.{field}: {target} has invalid or blocked joint limits")
                        )
                        continue
                    if value < lower * factor or value > upper * factor:
                        posture_problems.append(
                            (entity, name, f"{name}.{field}: {target} is outside its joint limits")
                        )
    if kind == "scene" and posture_problems:
        entity, location, _ = posture_problems[0]
        checks["S8"] = check(
            "S8", "fail", "; ".join(item[2] for item in posture_problems), entityKey=entity, location=location
        )
    else:
        applicable = kind == "scene" and bool(posture_groups) and stage is not None
        checks["S8"] = check(
            "S8",
            "pass" if applicable else "warn",
            "Taught joint counts and limits match the declared arm joints"
            if applicable
            else "No inspectable taught joint configuration is declared",
            notApplicable=kind == "asset",
        )
    items = []
    if kind == "asset":
        dressing = metadata.get("dressing", False)
        collisions = any(prim.IsA(UsdGeom.Gprim) and prim.HasAPI(UsdPhysics.CollisionAPI) for prim in prims)
        bodies = [prim for prim in prims if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
        inertial = bool(bodies) and all(
            UsdPhysics.MassAPI(prim).GetMassAttr().HasAuthoredValueOpinion()
            and UsdPhysics.MassAPI(prim).GetDiagonalInertiaAttr().HasAuthoredValueOpinion()
            for prim in bodies
        )
        definitions = (
            (
                "collision",
                "not_applicable" if dressing else "present" if collisions else "missing",
                "Authored collision geometry",
            ),
            (
                "massInertia",
                "not_applicable"
                if dressing
                else "warning"
                if inertial and (metadata.get("physics") or {}).get("massSource") == "estimated"
                else "present"
                if inertial
                else "missing",
                "Authored mass and inertia; estimated mass requires review"
                if (metadata.get("physics") or {}).get("massSource") == "estimated"
                else "Authored mass and inertia",
            ),
            (
                "visual",
                "missing" if not metadata.get("visual") else "warning" if visual_problem else "present",
                "PBR visual and visual budget",
            ),
            (
                "license",
                "missing" if not license_files else "warning" if provenance_problems else "present",
                "Original license and source hashes",
            ),
            (
                "jointSites",
                "warning"
                if reference_problems
                else "present"
                if any(p.IsA(UsdPhysics.Joint) for p in prims)
                else "not_applicable",
                "Named joints and sites",
            ),
            (
                "types",
                "missing" if not bindings else "warning" if reference_problems else "present",
                "Implemented entity types",
            ),
        )
        items = [
            {"id": identifier, "status": status, "message": message}
            for identifier, status, message in definitions
        ]
    entity_paths = {
        str(prim.GetPath())
        for prim in prims
        if str(prim.GetPath()).startswith("/World/Lab/")
        and (prim.HasAuthoredReferences() or prim.HasAPI(UsdPhysics.RigidBodyAPI))
    }
    untyped = sorted(
        path
        for path in entity_paths
        if not any(path == key or path.startswith(key + "/") for key in bound)
        and not any(path.startswith(root + "/") for root in entity_paths if root != path)
    )
    ordered = [checks[f"S{i}"] for i in range(1, 9)]
    ordered.append(
        {
            "id": "P1-P6",
            "tier": "physics",
            "status": "warn",
            "message": "Physics checks not run; no physics pass is claimed",
        }
    )
    result = {
        "schema": "lw.validation/1",
        "subject": payload["subject"],
        "ranAt": datetime.now(UTC).isoformat(),
        "toolVersions": {name: version(name) for name in ("usd-core", "pydantic", "pyyaml")},
        "checks": ordered,
        "launchable": not any(item["status"] == "fail" for item in ordered),
        "untypedEntities": untyped,
        "referencedAssets": [
            {"id": name, "contentHash": graph.assets[name]["contentHash"]}
            for name in sorted(graph.referenced)
        ],
        "inspection": "CPU USD structure only; no physics engine was loaded",
    }
    if items:
        result["completeness"] = items
    if stage and kind == "scene":
        result |= {"entityCount": len(entity_paths), "bindingCount": len(bound)}
    return parse_report(result)


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = validate(payload)
    Path(sys.argv[2]).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
