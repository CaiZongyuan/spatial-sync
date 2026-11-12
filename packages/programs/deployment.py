"""Rebuild committed authority, then verify the actual Runner Catalog and each artifact."""

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from packages.info.plans import PlanError
from packages.info.program import program_supports_type
from packages.library.archive import destination_parent
from packages.library.assets import AssetLibrary
from packages.library.common import BUILTIN_ROOT, LibraryError
from packages.library.common import document as yaml_document
from packages.library.scenes import SceneLibrary
from packages.policies.library import PolicyLibrary, read_json
from packages.policies.observations import ObservationSpec, remap_observation_spec_entity
from packages.policies.subjects import (
    _validate_scene_action_channels,
    fixed_bindings,
    policy_programs,
)
from packages.programs.simulation import material_bindings, validate_candidate_material
from packages.programs.subjects import fingerprint
from packages.training.deployment_authority import read_committed_permit


@dataclass(frozen=True)
class VerifiedDeployment:
    permit: dict
    bindings: dict


def _remap_entity_value(value, src_entity: str, dst_entity: str):
    if isinstance(value, str):
        if value == src_entity:
            return dst_entity
        if value.startswith(src_entity + "/"):
            return dst_entity + value[len(src_entity) :]
        return value
    if isinstance(value, list):
        return [_remap_entity_value(item, src_entity, dst_entity) for item in value]
    if isinstance(value, dict):
        return {
            (
                _remap_entity_value(k, src_entity, dst_entity) if isinstance(k, str) else k
            ): _remap_entity_value(v, src_entity, dst_entity)
            for k, v in value.items()
        }
    return deepcopy(value)


def _binding_without_scene_locations(
    binding: dict, *, src_entity: str | None = None, dst_entity: str | None = None
) -> dict:
    remapped = (
        _remap_entity_value(binding, src_entity, dst_entity)
        if src_entity and dst_entity and src_entity != dst_entity
        else deepcopy(binding)
    )
    if isinstance(remapped.get("config"), dict):
        cfg = {k: v for k, v in remapped["config"].items() if k != "locations"}
        program_cls = policy_programs().get(remapped.get("program"))
        if program_cls is not None and hasattr(program_cls, "Config"):
            cfg = program_cls.Config.model_validate(cfg).model_dump(mode="json")
            cfg.pop("locations", None)
        remapped["config"] = cfg
    return remapped


def _bound_policy_ids(binding: dict) -> list[str]:
    config = binding.get("config", {})
    policies_map = config.get("policies")
    if isinstance(policies_map, dict) and policies_map:
        return list(policies_map.values())
    if isinstance(config.get("policy"), str):
        return [config["policy"]]
    return []


def _roots_for_scene(scene, state_root) -> dict[str, Path]:
    roots = dict(AssetLibrary(state_root).roots)
    if getattr(scene, "root", None) == "user" and getattr(scene, "path", None) is not None:
        roots["user"] = Path(scene.path).resolve().parents[2]
    return roots


def _verify_scene_policy_action_channels(scene, binding: dict, roots: dict[str, Path]) -> None:
    program_cls = policy_programs().get(binding.get("program"))
    if program_cls is None:
        raise ValueError("Unknown Policy Program")
    target_key = binding["key"]
    library = PolicyLibrary(roots)
    pol_ids = _bound_policy_ids(binding)
    if not pol_ids:
        raise ValueError("Policy Program binding declares no Policy references")
    for pol_id in pol_ids:
        _, pkg = library.resolve(pol_id)
        task = read_json(pkg / "task.json")
        raw_spec = ObservationSpec.model_validate(yaml_document(pkg / "obs_spec.yaml"))
        spec = (
            remap_observation_spec_entity(raw_spec, task["robot"], target_key)
            if task["robot"] != target_key
            else raw_spec
        )
        _validate_scene_action_channels(scene, program_cls, binding["config"], spec)


def is_verified_builtin_policy_scene(scene) -> bool:
    """Allow verified built-in Reference Policy scenes without bypassing user deployment permits."""
    try:
        if (
            getattr(scene, "root", None) != "builtin"
            or "newton" not in scene.metadata.get("sources", [])
            or (scene.metadata.get("derivedFrom") or {}).get("promotion") is not None
        ):
            return False
        bindings = fixed_bindings(scene, "newton")
        reg = policy_programs()
        authorized = set(reg) | {"robot_arm.script"}
        mat_bindings = {k: b for k, b in bindings.items() if b["program"] in authorized}
        if not mat_bindings or any(b["program"] not in reg for b in mat_bindings.values()):
            return False
        roots = {"builtin": BUILTIN_ROOT}
        library = PolicyLibrary(roots)
        for binding in mat_bindings.values():
            program_cls = reg[binding["program"]]
            if not program_supports_type(program_cls, binding.get("type")):
                return False
            pol_ids = _bound_policy_ids(binding)
            if not pol_ids:
                return False
            for pol_id in pol_ids:
                rec, _ = library.resolve(pol_id)
                if rec.get("root") != "builtin":
                    return False
            _verify_scene_policy_action_channels(scene, binding, roots)
        return True
    except (LibraryError, ValueError, KeyError, TypeError, OSError):
        return False


def _validate_cross_scene_binding_against_permit(
    scene, target_key: str, binding: dict, permit: dict, roots: dict[str, Path]
) -> None:
    if (scene.metadata.get("derivedFrom") or {}).get("promotion") is not None:
        raise ValueError("Promotion-derived Scene must match its exact committed Scene snapshot")
    reg = policy_programs()
    if permit["candidateIdentity"].get("kind") != "policy" or binding.get("program") not in reg:
        raise ValueError("Cross-scene deployment only supports verified Policy Programs")
    materials = permit["materialIdentities"]
    src_target = permit["target"]["entityKey"]
    if set(materials) != {src_target}:
        raise ValueError("Cross-scene deployment requires a single committed robot composition")
    target_material = materials[src_target]
    expected_target_ok = (
        target_material.get("policies", {}).get(permit["target"]["action"]) == permit["candidateIdentity"]
        if isinstance(target_material, dict) and target_material.get("kind") == "policy-bundle"
        else target_material == permit["candidateIdentity"]
    )
    if not expected_target_ok or fingerprint(permit["binding"]) != permit["bindingHash"]:
        raise ValueError("Committed target material or binding hash differs")
    expected_binding = _binding_without_scene_locations(
        permit["binding"], src_entity=src_target, dst_entity=target_key
    )
    actual_binding = _binding_without_scene_locations(binding)
    if actual_binding != expected_binding:
        raise ValueError("Cross-scene target binding configuration differs from committed Controller")
    controller = permit["controller"]
    doc = controller["document"]
    if (
        doc["controller"] != controller["id"]
        or doc["version"] != controller["version"]
        or doc["scene"] != permit["scene"]["id"]
    ):
        raise ValueError("Controller document identity differs")
    validate_candidate_material(
        target_material, binding, roots, controller=doc, allow_entity_remap=True
    )
    _verify_scene_policy_action_channels(scene, binding, roots)


def authorize_cross_scene_policy_deployment(scene, *, state_root):
    """Authorize a new Scene reusing an exact human-approved Controller's Policy composition."""
    try:
        if (
            state_root is None
            or getattr(scene, "root", None) != "user"
            or "newton" not in scene.metadata.get("sources", [])
            or (scene.metadata.get("derivedFrom") or {}).get("promotion") is not None
        ):
            raise ValueError("Scene is ineligible for cross-scene Policy deployment")
        bindings = fixed_bindings(scene, "newton")
        reg = policy_programs()
        authorized = set(reg) | {"robot_arm.script"}
        mat_keys = [k for k, b in bindings.items() if b["program"] in authorized]
        if len(mat_keys) != 1 or bindings[mat_keys[0]]["program"] not in reg:
            raise ValueError("Cross-scene Policy deployment requires exactly one Policy Program binding")
        target_key = mat_keys[0]
        binding = bindings[target_key]
        promo_root = destination_parent(Path(state_root), ("controller-promotions",))
        if not promo_root.is_dir():
            raise ValueError("No committed Controller promotions exist")
        roots = _roots_for_scene(scene, state_root)
        matches = []
        for path in sorted(promo_root.glob("*.json")):
            try:
                promo_doc = yaml_document(path)
                lib_root = Path(promo_doc["context"]["libraryRoot"]).resolve()
                if lib_root != roots["user"]:
                    continue
                permit = read_committed_permit(state_root, path.stem)
                _validate_cross_scene_binding_against_permit(scene, target_key, binding, permit, roots)
            except (PlanError, LibraryError, ValueError, KeyError, TypeError, OSError):
                continue
            matches.append(permit)
        if not matches:
            raise ValueError("No committed human-approved Controller matches this Scene binding")
        for permit in reversed(matches):
            ctrl_head = roots["user"] / "controllers" / permit["controller"]["id"] / "controller.yaml"
            if ctrl_head.is_file():
                try:
                    if yaml_document(ctrl_head).get("currentVersion") == permit["controller"]["version"]:
                        return deepcopy(permit)
                except (ValueError, OSError):
                    pass
        return deepcopy(matches[-1])
    except (LibraryError, ValueError, KeyError, TypeError, OSError) as error:
        raise PlanError(
            "deployment_authorization_required",
            "Exact committed human deployment proof is unavailable",
        ) from error


def verify_runtime_deployment(scene, catalog, permit, *, state_root):
    try:
        if (
            state_root is None
            or permit["schema"] != "lw.deployment-permit/1"
            or fingerprint({key: value for key, value in permit.items() if key != "publicationHash"})
            != permit["publicationHash"]
        ):
            raise ValueError("Private deployment descriptor is unavailable or changed")
        actual = read_committed_permit(state_root, permit["promotionId"], permit["publicationHash"])
        if actual != permit:
            raise ValueError("Private payload differs from actual committed human authority")
        bindings = catalog.entities
        if permit["scene"] == {"id": scene.id, "contentHash": scene.content_hash}:
            if permit["appliedBindings"] != {key: fingerprint(binding) for key, binding in bindings.items()}:
                raise ValueError("Actual Scene or complete binding composition differs")
            target = permit["target"]["entityKey"]
            if (
                permit["binding"] != bindings.get(target)
                or fingerprint(permit["binding"]) != permit["bindingHash"]
            ):
                raise ValueError("Actual deployed target binding differs")
            materials = permit["materialIdentities"]
            target_material = materials.get(target)
            expected_target_ok = (
                target_material.get("policies", {}).get(permit["target"]["action"])
                == permit["candidateIdentity"]
                if isinstance(target_material, dict) and target_material.get("kind") == "policy-bundle"
                else target_material == permit["candidateIdentity"]
            )
            if set(materials) != set(material_bindings(catalog)) or not expected_target_ok:
                raise ValueError("Actual material coverage differs from committed composition")
            controller = permit["controller"]
            document = controller["document"]
            if (
                document["controller"] != controller["id"]
                or document["version"] != controller["version"]
                or document["scene"] != scene.id
            ):
                raise ValueError("Controller document identity differs from the actual Scene")
            roots = _roots_for_scene(scene, state_root)
            for key, identity in materials.items():
                validate_candidate_material(
                    identity, bindings[key], roots, controller=document if key == target else None
                )
        else:
            if scene is None or getattr(scene, "root", None) != "user":
                raise ValueError("Cross-scene deployment requires a validated user Scene")
            roots = _roots_for_scene(scene, state_root)
            resolved_scene = SceneLibrary(user_root=roots["user"], state_root=state_root).resolve(
                scene.id, launch=True, source="newton"
            )
            if resolved_scene.content_hash != scene.content_hash:
                raise ValueError("Cross-scene package changed after admission")
            expected_bindings = fixed_bindings(resolved_scene, "newton")
            if {k: fingerprint(v) for k, v in bindings.items()} != {
                k: fingerprint(v) for k, v in expected_bindings.items()
            }:
                raise ValueError("Runner Catalog differs from validated Scene bindings")
            mat_map = material_bindings(catalog)
            if len(mat_map) != 1:
                raise ValueError("Cross-scene deployment requires a single material binding")
            target_key, target_binding = next(iter(mat_map.items()))
            _validate_cross_scene_binding_against_permit(
                resolved_scene, target_key, target_binding, permit, roots
            )
    except (KeyError, ValueError, TypeError) as error:
        raise LibraryError(
            "simulation_deployment_changed", "Actual deployed composition is unavailable or differs", 409
        ) from error
    return VerifiedDeployment(deepcopy(permit), deepcopy(bindings))
