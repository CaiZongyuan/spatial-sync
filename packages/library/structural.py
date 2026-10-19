"""S1–S8 in a CPU-only worker. Composition only sees preflighted layer copies."""

from __future__ import annotations

import hashlib
import json
import math
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

from packages.info.binding_validation import compile_alert_rules, validate_binding_semantics
from packages.info.declarations import EntityBindingDeclaration
from packages.library.common import content_hash, document
from packages.library.metadata import Locations
from packages.library.python_stages import resources as python_resources
from packages.library.reports import parse_report
from packages.library.templates import TemplateError, instantiate, references
from packages.library.templates import resolve as resolve_templates
from packages.library.visuals import glb_budget
from packages.programs.robot_arm.taught import posture_problems as posture_problems_for


def check(identifier, status, message, **details):
    return {"id": identifier, "tier": "structural", "status": status, "message": message, **details}


def entity_path(path, *, asset=False):
    """USD guarantees path syntax and uniqueness; the public identity additionally requires ASCII."""
    root = "/Asset" if asset else "/World/Lab"
    return path.isascii() and (path == root or path.startswith(root + "/"))


def config_locations(value, package):
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
    return value


def checked_config(program, config, kind):
    if not program.get("configModule"):
        return config  # Small SDK fixtures supply role metadata without a runtime Config class.
    owner = import_module(program["configModule"])
    for part in program["configClass"].split("."):
        owner = getattr(owner, part)
    try:
        return owner.Config.model_validate(config).model_dump(mode="json")
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
        return config


def usd_resources(stage):
    prims = list(stage.Traverse(Usd.TraverseInstanceProxies()))
    joints = {}
    for prim in prims:
        if not prim.IsA(UsdPhysics.Joint):
            continue
        if prim.IsA(UsdPhysics.RevoluteJoint):
            joint, factor, kind = UsdPhysics.RevoluteJoint(prim), math.pi / 180, "revolute"
        elif prim.IsA(UsdPhysics.PrismaticJoint):
            joint, factor, kind = UsdPhysics.PrismaticJoint(prim), 1, "prismatic"
        else:
            joints[str(prim.GetPath())] = {"kind": "non-scalar", "limits": None}
            continue
        lower, upper = joint.GetLowerLimitAttr().Get(), joint.GetUpperLimitAttr().Get()
        valid = (
            type(lower) in (int, float)
            and type(upper) in (int, float)
            and not math.isnan(lower)
            and not math.isnan(upper)
            and lower <= upper
        )
        joints[str(prim.GetPath())] = {
            "kind": kind,
            "limits": [lower * factor, upper * factor] if valid else None,
            **({"limitError": "invalid or blocked joint limits"} if not valid else {}),
        }
    return {
        "entities": [str(prim.GetPath()) for prim in prims],
        "bodies": [str(prim.GetPath()) for prim in prims if prim.HasAPI(UsdPhysics.RigidBodyAPI)],
        "sites": [
            str(prim.GetPath())
            for prim in prims
            if prim.IsA(UsdGeom.Xformable)
            and not prim.HasAPI(UsdPhysics.RigidBodyAPI)
            and not prim.HasAPI(UsdPhysics.CollisionAPI)
        ],
        "joints": joints,
    }


class USDGraph:
    """Preflight and rewrite all layer asset paths before opening a composed stage."""

    def __init__(self, package, assets, expected=()):
        self.package = package
        self.assets = {asset["id"]: asset for asset in assets}
        self.library_files = {
            f"assets/{asset['id'].replace('@', '/')}/{filename}": (asset, filename)
            for asset in assets
            for filename in asset["files"]
        }
        self.clones, self.errors, self.referenced = {}, [], set()
        self.verified = set()
        self.expected = {value["id"]: value["contentHash"] for value in expected}

    def resolve(self, owner, filename, reference):
        if not reference:
            return None
        if Ar.IsPackageRelativePath(reference):
            raise ValueError("Unsupported packaged USD member reference; upload unpacked USD layers")
        if reference.startswith("library:"):
            target = self.library_files.get(reference.removeprefix("library:"))
            if target is None:
                raise ValueError(f"Unknown indexed library reference: {reference}")
            if (
                target[0]["id"] in self.expected
                and target[0]["contentHash"] != self.expected[target[0]["id"]]
            ):
                raise ValueError("Referenced immutable asset changed: " + target[0]["id"])
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


def validate(payload):
    kind, metadata, package = payload["kind"], payload["metadata"], payload["package"]
    checks = {"S1": check("S1", "pass", "Package metadata and declared files match their schemas")}
    graph = USDGraph(package, payload["assets"], payload.get("expectedAssets", []))
    stage, parse_error = None, None
    scene_source = (metadata.get("sources") or [None])[0]
    authored = payload.get("bindings", {})
    authored = {
        "scene": authored.get("scene", payload["subject"].split(":", 1)[1].split("@")[0]),
        "entities": authored.get("entities", []),
    }
    resolved, template_error = None, None
    declarations = authored
    if kind == "scene":
        try:
            # A failed implements/source selection must not discard already known package bytes.
            graph.referenced.update(name for name in references(authored) if name in graph.assets)
            resolved = resolve_templates(
                authored, scene_source, graph.assets, payload.get("expectedAssets", [])
            )
            declarations = resolved.bootstrap()
        except (TemplateError, OSError, ValueError) as exc:
            template_error = str(exc)
            declarations = authored | {
                "entities": [binding for binding in authored["entities"] if not binding.get("template")]
            }
    python_stage = metadata["stage"].startswith("python:")
    resources, python_error = None, None
    if python_stage:
        try:
            resources = python_resources(metadata, package, declarations)
            if "usdPath" in resources:
                path = Path(resources["usdPath"])
                scope = {
                    "path": str(path.parent),
                    "files": {p.name: str(p) for p in path.parent.iterdir() if p.is_file()},
                }
                resources = usd_resources(USDGraph(scope, []).compose(path.name))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            python_error = "Python builder resources could not be inspected: " + str(exc)
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
    prims = list(stage.Traverse(Usd.TraverseInstanceProxies())) if stage else []
    if stage:
        resources = usd_resources(stage)
    resource_entities = set((resources or {}).get("entities", []))
    resource_roles = {
        "body": set((resources or {}).get("bodies", [])),
        "site": set((resources or {}).get("sites", [])),
        "joint": set((resources or {}).get("joints", {})),
    }
    resource_keys = resource_entities | set().union(*resource_roles.values())
    if kind == "scene" and resolved is not None and resources is not None:
        try:
            declarations = instantiate(resolved, resources, payload["programs"])
        except TemplateError as exc:
            template_error = str(exc)
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
    bindings = declarations["entities"] if kind == "scene" else metadata.get("implements", [])
    reference_problems, posture_groups, bound = [], [], set()
    if template_error:
        reference_problems.append(template_error)
    if python_error:
        reference_problems.append(python_error)
    semantic_bindings, normalised_bindings = {}, {}
    types = payload["types"]
    for binding in bindings:
        entity = binding.get("key", "/Asset") if kind == "scene" else "/Asset"
        entities = [entity]
        if kind == "scene":
            selector = binding.get("match") or entity
            if not isinstance(selector, str) or not (
                selector.isascii() and selector.startswith("/World/")
                if python_stage
                else entity_path(selector)
            ):
                reference_problems.append("Binding selector must use an ASCII /World/Lab entity path")
            if binding.get("match"):
                entities = sorted(key for key in resource_entities if fnmatchcase(key, selector))
                if resources is not None and not entities:
                    reference_problems.append("Binding selector matched no entities: " + selector)
            elif resources is not None and entity not in resource_entities and not binding.get("unplaced"):
                reference_problems.append("Binding entity does not exist: " + str(entity))
            for key in entities:
                if key in bound:
                    reference_problems.append("Duplicate binding: " + key)
                bound.add(key)
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
                        config[field] = config_locations(config[field], package)
                    except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
                        reference_problems.append(str(exc))
                        config[field] = {"locations": {}}
            try:
                config = checked_config(program, config, kind)
            except ValueError as exc:
                reference_problems.append(str(exc))
            posture_paths = None
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
                    matches = sorted(key for key in resource_keys if fnmatchcase(key, path))
                    if not matches and resources is not None:
                        reference_problems.append(f"{field} references a missing {role}: {path}")
                    for target in matches:
                        if target not in resource_roles[role]:
                            reference_problems.append(f"{field} does not reference a {role}: {target}")
                        paths.append(target)
                if information.get("posture"):
                    if len(paths) != len(set(paths)):
                        reference_problems.append(f"{field} contains duplicate joints")
                    posture_paths = paths
            if posture_paths is not None or "taughtLimits" in program:
                taught = config.get("locations", payload.get("locations", {}))
                if isinstance(taught, dict):
                    if taught.get("robot") is not None and taught["robot"] not in entities:
                        reference_problems.append(
                            "Location document belongs to another robot: " + taught["robot"]
                        )
                    else:
                        for robot in [taught["robot"]] if taught.get("robot") else entities:
                            posture_groups.append(
                                (robot, posture_paths or [], taught, program.get("taughtLimits"))
                            )
            if kind == "scene":
                semantic_bindings.update({key: binding | {"config": config} for key in entities})
    if kind == "scene":

        def property_schema(entity, prop):
            return types[semantic_bindings[entity]["type"]]["properties"][prop]["schema"]

        for key, binding in semantic_bindings.items():
            definition = types.get(binding.get("type"), {})
            if not definition:
                continue
            try:
                if resources is not None:
                    validate_binding_semantics(
                        key, binding, definition, property_schema, resource_roles["site"]
                    )
                compile_alert_rules(key, binding, definition, property_schema)
                normalised_bindings[key] = EntityBindingDeclaration.model_validate(binding).model_dump(
                    mode="json", exclude_none=True
                )
            except (ValueError, KeyError, TypeError) as exc:
                reference_problems.append(f"Invalid binding semantics on {key}: {exc}")
    checks["S5"] = check(
        "S5",
        "fail" if reference_problems else "warn" if resources is None else "pass",
        "; ".join(dict.fromkeys(reference_problems))
        or (
            "Program/type declarations checked; stage resources were not inspected"
            if resources is None
            else "Declared types and Config spatial references exist"
        ),
    )
    visual_problem, triangles = None, None
    if kind == "asset" and metadata.get("visual"):
        try:
            triangles = glb_budget(package["files"][metadata["visual"]], package)
        except (KeyError, ValueError, TypeError, IndexError, OSError) as exc:
            visual_problem = str(exc)
        checks["S6"] = check(
            "S6",
            "fail" if visual_problem else "pass",
            visual_problem or f"Visual budget: {triangles} triangles; package-local textures <=4096x4096",
        )
    elif kind == "asset":
        checks["S6"] = check("S6", "warn", "PBR visual is missing")
    else:
        dressing_prims = [prim for prim in prims if str(prim.GetPath()).startswith("/World/Dressing/")]
        dressing = [prim for prim in dressing_prims if prim.HasAuthoredReferences()]
        checks["S6"] = check(
            "S6",
            "warn" if stage is None or dressing_prims else "pass",
            "Dressing visual budget was not inspected because the USD stage is unavailable"
            if stage is None
            else f"Dressing has {len(dressing)} USD reference groups; merged GLB count has not been checked"
            if dressing_prims
            else "No Dressing visual groups are declared",
            **({"dressingReferenceCount": len(dressing)} if stage is not None else {}),
        )
    posture_problems = []
    for entity, joints, taught, override_limits in posture_groups:
        limits = override_limits
        if limits is None:
            limits = []
            for target in joints:
                information = (resources or {}).get("joints", {}).get(target, {})
                bounds = information.get("limits")
                if information.get("kind") not in ("revolute", "prismatic") or bounds is None:
                    message = information.get("limitError", "has no inspectable scalar joint limits")
                    for name in taught.get("locations", {}):
                        posture_problems.append((entity, name, f"{name}: {target}: {message}"))
                    bounds = (None, None)
                limits.append(bounds)
        posture_problems.extend(
            (entity, name, message) for name, message in posture_problems_for(taught, limits)
        )
    if kind == "scene" and posture_problems:
        entity, location, _ = posture_problems[0]
        checks["S8"] = check(
            "S8", "fail", "; ".join(item[2] for item in posture_problems), entityKey=entity, location=location
        )
    else:
        applicable = kind == "scene" and bool(posture_groups)
        checks["S8"] = check(
            "S8",
            "pass" if applicable else "warn",
            "Taught joint counts and limits match the declared arm joints"
            if applicable
            else "No inspectable taught joint configuration is declared",
            notApplicable=kind == "asset" or (resources is not None and not posture_groups),
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
            {"id": name, "contentHash": graph.expected[name]
             if name in graph.expected else graph.assets[name]["contentHash"]}
            for name in sorted(graph.referenced | graph.expected.keys())
        ],
        "inspection": "CPU USD structure only; no physics engine was loaded",
    }
    if items:
        result["completeness"] = items
    if kind == "scene" and scene_source is not None and checks["S5"]["status"] == "pass":
        result["effectiveBindings"] = {scene_source: list(normalised_bindings.values())}
    if stage and kind == "scene":
        result |= {"entityCount": len(entity_paths), "bindingCount": len(bound)}
    return parse_report(result)


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = validate(payload)
    Path(sys.argv[2]).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
