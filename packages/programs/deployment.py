"""Rebuild committed authority, then verify the actual Runner Catalog and each artifact."""

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from pxr import Gf, Usd, UsdGeom, UsdPhysics

from packages.info.plans import PlanError
from packages.library import common as library_common
from packages.library.archive import destination_parent
from packages.library.assets import AssetLibrary
from packages.library.common import LibraryError
from packages.library.common import document as yaml_document
from packages.library.robot_resources import (
    Controller,
    ControllerVersion,
    Embodiment,
    RobotModel,
    RobotResources,
)
from packages.library.scenes import SceneLibrary
from packages.library.structural import USDGraph
from packages.library.templates import load_snapshots, verify_snapshot
from packages.policies.library import PolicyLibrary, read_json
from packages.policies.observations import (
    ObservationSpec,
    remap_observation_spec_entity,
    spec_within_robot_entity,
)
from packages.policies.subjects import (
    CandidateIdentity,
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


def _builtin_root() -> Path:
    return Path(library_common.BUILTIN_ROOT).resolve()


def _builtin_scene_library(builtin_root: Path, *, state_root=None) -> SceneLibrary:
    return SceneLibrary(
        builtin_root=builtin_root,
        user_root=builtin_root / "__no_user_root__",
        state_root=state_root,
    )


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


def _single_policy_program_binding(bindings: dict) -> tuple[str, dict]:
    reg = policy_programs()
    authorized = set(reg) | {"robot_arm.script"}
    mat_keys = [key for key, item in bindings.items() if item["program"] in authorized]
    if len(mat_keys) != 1 or bindings[mat_keys[0]]["program"] not in reg:
        raise ValueError("Deployment requires exactly one Policy Program binding")
    key = mat_keys[0]
    return key, bindings[key]


def _target_material_matches_candidate(target_material, permit: dict) -> bool:
    if isinstance(target_material, dict) and target_material.get("kind") == "policy-bundle":
        return (
            target_material.get("policies", {}).get(permit["target"]["action"])
            == permit["candidateIdentity"]
        )
    return target_material == permit["candidateIdentity"]


def _roots_for_scene(scene, state_root) -> dict[str, Path]:
    roots = dict(AssetLibrary(state_root, builtin_root=_builtin_root()).roots)
    if getattr(scene, "root", None) == "user" and getattr(scene, "path", None) is not None:
        roots["user"] = Path(scene.path).resolve().parents[2]
    return roots


def _rigid_transform(matrix) -> None:
    vectors = [Gf.Vec3d(*(matrix[i][j] for j in range(3))) for i in range(3)]
    tolerance = 64 * math.ulp(1.0)
    if (
        any(
            abs(Gf.Dot(a, b) - (i == j)) > tolerance
            for i, a in enumerate(vectors)
            for j, b in enumerate(vectors)
        )
        or matrix.GetDeterminant() < 0
    ):
        raise ValueError("Target robot base placement transform must be rigid")


def _embodiment_usd_signature(stage, root: str) -> str:
    """Physical, drive, joint, collision, and site USD subtree digest relative to placement root."""
    root_prim = stage.GetPrimAtPath(root)
    if not root_prim or not root_prim.IsValid():
        raise ValueError("Target robot root prim is missing")
    interesting = [
        prim
        for prim in Usd.PrimRange(root_prim, Usd.TraverseInstanceProxies())
        if prim.HasAPI(UsdPhysics.RigidBodyAPI)
        or prim.HasAPI(UsdPhysics.MassAPI)
        or prim.HasAPI(UsdPhysics.CollisionAPI)
        or prim.HasAPI(UsdPhysics.ArticulationRootAPI)
        or prim.HasAPI(UsdPhysics.MaterialAPI)
        or prim.IsA(UsdPhysics.Joint)
        or prim.IsA(UsdGeom.Xformable)
    ]
    selected = {}
    for prim in interesting:
        cur = prim
        while cur and (str(cur.GetPath()) == root or str(cur.GetPath()).startswith(root + "/")):
            selected[str(cur.GetPath())] = cur
            cur = cur.GetParent()

    def relative(path) -> str:
        value = str(path)
        return "." + value[len(root) :] if value == root or value.startswith(root + "/") else value

    rows = []
    for path, prim in sorted(selected.items()):
        attributes = {}
        for attribute in prim.GetAttributes():
            name = attribute.GetName()
            if path == root and (name.startswith("xformOp:") or name == "xformOpOrder"):
                continue
            if attribute.ValueMightBeTimeVarying():
                raise ValueError("Robot physical USD subtree cannot use animated attributes")
            if (
                name.startswith(("physics:", "drive:", "newton:", "physx", "state:", "xformOp:"))
                or name == "xformOpOrder"
                or (
                    prim.HasAPI(UsdPhysics.CollisionAPI)
                    and name
                    in (
                        "points",
                        "faceVertexCounts",
                        "faceVertexIndices",
                        "holeIndices",
                        "subdivisionScheme",
                        "orientation",
                        "size",
                        "radius",
                        "height",
                        "axis",
                    )
                )
            ):
                attributes[name] = str(attribute.Get())
        rows.append(
            {
                "path": relative(path),
                "type": prim.GetTypeName(),
                "apis": sorted(prim.GetAppliedSchemas()),
                "attributes": attributes,
                "relations": {
                    relation.GetName(): [relative(target) for target in relation.GetTargets()]
                    for relation in prim.GetRelationships()
                    if relation.GetName().startswith(("physics:", "newton:", "material:binding"))
                },
            }
        )
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def _verify_embodiment_usd_subtree(
    scene, target_key: str, controller_doc: dict, roots: dict[str, Path]
) -> None:
    emb_id = controller_doc["embodiment"]
    RobotResources._name(emb_id)
    embodiment = None
    for root in roots.values():
        resolved_root = Path(root).resolve()
        candidate_file = destination_parent(resolved_root, ("embodiments",)) / f"{emb_id}.yaml"
        if candidate_file.exists() or candidate_file.is_symlink():
            embodiment = RobotResources._read(
                resolved_root, Path("embodiments") / f"{emb_id}.yaml", Embodiment, emb_id
            )
    if (
        embodiment is None
        or embodiment["kind"] != "sim"
        or embodiment["controller"] != controller_doc["controller"]
    ):
        raise ValueError("Committed Embodiment metadata is unavailable or differs")
    asset_id = embodiment["asset"]
    asset_snapshot = load_snapshots(roots, [asset_id])[asset_id]
    verify_snapshot(asset_snapshot)
    payload = scene.runtime_payload()
    stage_spec = payload["metadata"]["stage"]
    if stage_spec.startswith("python:"):
        allowed_scenes = {controller_doc["scene"]} | {
            item["scene"] for item in embodiment["bindings"] if item["source"] == "newton"
        }
        if scene.id not in allowed_scenes or not any(
            item["entity"] == target_key and item["source"] == "newton"
            for item in embodiment["bindings"]
        ):
            raise ValueError("Python stage cross-scene Embodiment binding is unsupported")
        return
    expected_map = {item["id"]: item["contentHash"] for item in payload["expectedAssets"]}
    if expected_map.get(asset_id) != asset_snapshot["contentHash"]:
        raise ValueError("Target Scene does not pin the Embodiment's immutable robot Asset")
    assets_by_id = {item["id"]: item for item in payload["assets"]}
    assets_by_id[asset_id] = asset_snapshot
    graph = USDGraph(payload["package"], list(assets_by_id.values()), payload["expectedAssets"])
    stage = graph.compose(stage_spec)
    if stage is None or graph.errors:
        raise ValueError("; ".join(graph.errors) or "Target Scene USD stage cannot be composed")
    asset_layer = graph.layer(asset_snapshot, asset_snapshot["metadata"]["stage"])
    canonical = Usd.Stage.Open(asset_layer)
    if canonical is None or graph.errors or canonical.GetCompositionErrors():
        raise ValueError("Canonical Embodiment Asset USD stage cannot be composed")
    prim = stage.GetPrimAtPath(target_key)
    if (
        not prim
        or not prim.IsValid()
        or not any(
            spec.layer.identifier == asset_layer.identifier and str(spec.path) == "/Asset"
            for spec in prim.GetPrimStack()
        )
    ):
        raise ValueError("Target robot entity does not reference the Embodiment's canonical /Asset")
    _rigid_transform(UsdGeom.XformCache().GetLocalToWorldTransform(prim))
    if _embodiment_usd_signature(canonical, "/Asset") != _embodiment_usd_signature(stage, target_key):
        raise ValueError("Target robot USD subtree differs from canonical Embodiment Asset")


def _verify_scene_policy_action_channels(
    scene,
    binding: dict,
    roots: dict[str, Path],
    *,
    require_robot_scope: bool = False,
) -> None:
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
        if require_robot_scope or task["robot"] != target_key:
            if not spec_within_robot_entity(raw_spec, task["robot"]):
                raise ValueError("Cross-scene Policy channels reference external entities outside robot")
        spec = (
            remap_observation_spec_entity(raw_spec, task["robot"], target_key)
            if task["robot"] != target_key
            else raw_spec
        )
        _validate_scene_action_channels(scene, program_cls, binding["config"], spec)


def _controller_superseded_in_user(
    user_root: Path | None, controller_id: str, builtin_version: int
) -> bool:
    if user_root is None:
        return False
    resolved_user = Path(user_root).resolve()
    head = destination_parent(resolved_user, ("controllers", controller_id)) / "controller.yaml"
    if not head.exists() and not head.is_symlink():
        return False
    try:
        meta = RobotResources._read(
            resolved_user,
            Path("controllers") / controller_id / "controller.yaml",
            Controller,
            controller_id,
        )
    except LibraryError:
        return True
    return meta["currentVersion"] != builtin_version


def _build_builtin_controller_permit(
    controller_id: str,
    *,
    builtin_root: Path | None = None,
    state_root=None,
    publication_hash: str | None = None,
) -> dict:
    b_root = (Path(builtin_root) if builtin_root is not None else _builtin_root()).resolve()
    RobotResources._name(controller_id)
    ctrl_meta = RobotResources._read(
        b_root, Path("controllers") / controller_id / "controller.yaml", Controller, controller_id
    )
    ver_num = ctrl_meta["currentVersion"]
    if ver_num is None:
        raise ValueError("Built-in Controller has no active Reference version")
    ver_rec = RobotResources._read(
        b_root, Path("controllers") / controller_id / f"{ver_num}.yaml", ControllerVersion, controller_id
    )
    ver_hash = ver_rec["contentHash"]
    doc = {k: v for k, v in ver_rec.items() if k != "contentHash"}
    if (
        doc["controller"] != controller_id
        or doc["version"] != ver_num
        or doc["embodiment"] != ctrl_meta["embodiment"]
        or doc.get("policy") is None
        or doc.get("programArtifact") is not None
    ):
        raise ValueError("Built-in Controller version metadata differs")
    model_doc = RobotResources._read(
        b_root, Path("robot-models") / f"{doc['robotModel']}.yaml", RobotModel, doc["robotModel"]
    )
    if (
        doc["embodiment"] not in model_doc["embodiments"]
        or not model_doc["runnable"]
        or model_doc["capability"]["program"] != doc["program"]
    ):
        raise ValueError("Built-in RobotModel capability differs from Controller")

    base_scene = _builtin_scene_library(b_root, state_root=state_root).resolve(
        doc["scene"], launch=True, source="newton"
    )
    if (
        base_scene.root != "builtin"
        or not Path(base_scene.path).resolve().is_relative_to(b_root)
        or "newton" not in base_scene.metadata.get("sources", [])
        or (base_scene.metadata.get("derivedFrom") or {}).get("promotion") is not None
    ):
        raise ValueError("Built-in Controller base Scene is invalid")

    base_bindings = fixed_bindings(base_scene, "newton")
    target_key, src_binding = _single_policy_program_binding(base_bindings)
    if src_binding["program"] != doc["program"]:
        raise ValueError("Built-in Controller Program differs from base Scene binding")

    builtin_roots = {"builtin": b_root}
    pol_lib = PolicyLibrary(builtin_roots)

    def _builtin_policy_identity(pol_id: str) -> tuple[dict, Path]:
        rec, pkg = pol_lib.resolve(pol_id)
        if not pkg.resolve().is_relative_to(b_root):
            raise ValueError("Built-in Policy resolves outside BUILTIN_ROOT")
        fields = {name: rec[name] for name in CandidateIdentity.model_fields if name in rec}
        return CandidateIdentity(**fields).model_dump(mode="json"), pkg

    candidate_identity, primary_pkg = _builtin_policy_identity(doc["policy"])
    primary_task = read_json(primary_pkg / "task.json")
    iface, target_action = primary_task["implements"].rsplit(".", 1)
    if iface != src_binding["type"]:
        raise ValueError("Built-in Policy interface differs from base Scene binding")

    cfg_policies = src_binding.get("config", {}).get("policies")
    if isinstance(cfg_policies, dict) and cfg_policies:
        if not isinstance(doc.get("policies"), dict) or set(doc["policies"]) != set(cfg_policies):
            raise ValueError("Built-in multi-policy Controller composition differs from base Scene")
        bundle = {}
        for act, pol_id in sorted(cfg_policies.items()):
            ident, _ = _builtin_policy_identity(pol_id)
            bundle[act] = ident
        if bundle.get(target_action) != candidate_identity:
            raise ValueError("Built-in primary Policy differs from bundle slot")
        target_material = {"kind": "policy-bundle", "policies": bundle}
    else:
        if doc.get("policies") is not None:
            raise ValueError("Single-policy built-in binding cannot carry multi-policy composition")
        target_material = candidate_identity

    validate_candidate_material(
        target_material, src_binding, builtin_roots, controller=doc, allow_entity_remap=True
    )
    _verify_scene_policy_action_channels(base_scene, src_binding, builtin_roots)
    _verify_embodiment_usd_subtree(base_scene, target_key, doc, builtin_roots)

    permit = {
        "schema": "lw.builtin-deployment-permit/1",
        "scene": {"id": base_scene.id, "contentHash": base_scene.content_hash},
        "controller": {
            "id": doc["controller"],
            "version": doc["version"],
            "contentHash": ver_hash,
            "document": doc,
        },
        "candidateIdentity": candidate_identity,
        "materialIdentities": {target_key: target_material},
        "target": {
            "entityKey": target_key,
            "interface": src_binding["type"],
            "action": target_action,
        },
        "binding": deepcopy(src_binding),
        "bindingHash": fingerprint(src_binding),
        "appliedBindings": {k: fingerprint(v) for k, v in base_bindings.items()},
    }
    permit["publicationHash"] = fingerprint(permit)
    if publication_hash is not None and permit["publicationHash"] != publication_hash:
        raise ValueError("Private deployment transport differs from actual built-in authority")
    return permit


def authorize_builtin_policy_deployment(scene, *, state_root=None) -> dict:
    """Issue a verified lw.builtin-deployment-permit/1 for a built-in Reference Policy Scene."""
    try:
        b_root = _builtin_root()
        if (
            getattr(scene, "root", None) != "builtin"
            or getattr(scene, "path", None) is None
            or not Path(scene.path).resolve().is_relative_to(b_root)
            or "newton" not in scene.metadata.get("sources", [])
            or (scene.metadata.get("derivedFrom") or {}).get("promotion") is not None
        ):
            raise ValueError("Scene is not an eligible built-in Reference Policy Scene")
        resolved_scene = _builtin_scene_library(b_root, state_root=state_root).resolve(
            scene.id, launch=True, source="newton"
        )
        if resolved_scene.root != "builtin" or resolved_scene.content_hash != scene.content_hash:
            raise ValueError("Built-in Scene package differs from indexed built-in snapshot")
        bindings = fixed_bindings(resolved_scene, "newton")
        target_key, binding = _single_policy_program_binding(bindings)
        builtin_roots = {"builtin": b_root}
        pol_lib = PolicyLibrary(builtin_roots)
        pol_ids = _bound_policy_ids(binding)
        if not pol_ids:
            raise ValueError("Built-in Policy Program binding declares no Policy references")
        for pol_id in pol_ids:
            _, pkg = pol_lib.resolve(pol_id)
            if not pkg.resolve().is_relative_to(b_root):
                raise ValueError("Built-in Policy resolves outside BUILTIN_ROOT")

        ctrl_dir = destination_parent(b_root, ("controllers",))
        if not ctrl_dir.is_dir():
            raise ValueError("No built-in Controllers exist")
        exact_matches = []
        cross_matches = []
        for ctrl_file in sorted(ctrl_dir.glob("*/controller.yaml")):
            cid = ctrl_file.parent.name
            try:
                permit = _build_builtin_controller_permit(
                    cid, builtin_root=b_root, state_root=state_root
                )
            except (PlanError, LibraryError, ValueError, KeyError, TypeError, OSError):
                continue
            if permit["scene"] == {
                "id": resolved_scene.id,
                "contentHash": resolved_scene.content_hash,
            }:
                if permit["appliedBindings"] == {k: fingerprint(v) for k, v in bindings.items()}:
                    exact_matches.append(permit)
            else:
                try:
                    _validate_cross_scene_binding_against_permit(
                        resolved_scene, target_key, binding, permit, builtin_roots
                    )
                except (PlanError, LibraryError, ValueError, KeyError, TypeError, OSError):
                    continue
                cross_matches.append(permit)
        if len(exact_matches) == 1:
            return deepcopy(exact_matches[0])
        if len(exact_matches) == 0 and len(cross_matches) == 1:
            return deepcopy(cross_matches[0])
        raise ValueError("Built-in Scene matches no unique built-in Reference Controller")
    except (PlanError, LibraryError, ValueError, KeyError, TypeError, OSError) as error:
        raise PlanError(
            "deployment_authorization_required",
            "Verified built-in Reference Controller deployment proof is unavailable",
        ) from error


def is_verified_builtin_policy_scene(scene, *, state_root=None) -> bool:
    """Return True iff scene is authorized by a verified built-in Reference Controller permit."""
    try:
        authorize_builtin_policy_deployment(scene, state_root=state_root)
        return True
    except PlanError:
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
    if (
        not _target_material_matches_candidate(target_material, permit)
        or fingerprint(permit["binding"]) != permit["bindingHash"]
    ):
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
    auth_roots = (
        {"builtin": _builtin_root()}
        if permit.get("schema") == "lw.builtin-deployment-permit/1"
        else roots
    )
    validate_candidate_material(
        target_material, binding, auth_roots, controller=doc, allow_entity_remap=True
    )
    _verify_scene_policy_action_channels(scene, binding, auth_roots, require_robot_scope=True)
    _verify_embodiment_usd_subtree(scene, target_key, doc, auth_roots)


def authorize_cross_scene_policy_deployment(scene, *, state_root):
    """Authorize a new Scene reusing an exact human-approved or built-in Reference Controller."""
    try:
        if (
            state_root is None
            or getattr(scene, "root", None) != "user"
            or "newton" not in scene.metadata.get("sources", [])
            or (scene.metadata.get("derivedFrom") or {}).get("promotion") is not None
        ):
            raise ValueError("Scene is ineligible for cross-scene Policy deployment")
        bindings = fixed_bindings(scene, "newton")
        target_key, binding = _single_policy_program_binding(bindings)
        roots = _roots_for_scene(scene, state_root)
        promo_root = destination_parent(Path(state_root), ("controller-promotions",))
        matches = []
        if promo_root.is_dir():
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
        if matches:
            for permit in reversed(matches):
                ctrl_head = roots["user"] / "controllers" / permit["controller"]["id"] / "controller.yaml"
                if ctrl_head.is_file():
                    try:
                        if (
                            yaml_document(ctrl_head).get("currentVersion")
                            == permit["controller"]["version"]
                        ):
                            return deepcopy(permit)
                    except (ValueError, OSError):
                        pass
            return deepcopy(matches[-1])

        b_root = _builtin_root()
        ctrl_dir = destination_parent(b_root, ("controllers",))
        builtin_matches = []
        if ctrl_dir.is_dir():
            for ctrl_file in sorted(ctrl_dir.glob("*/controller.yaml")):
                cid = ctrl_file.parent.name
                try:
                    permit = _build_builtin_controller_permit(
                        cid, builtin_root=b_root, state_root=state_root
                    )
                    if _controller_superseded_in_user(
                        roots.get("user"), cid, permit["controller"]["version"]
                    ):
                        continue
                    _validate_cross_scene_binding_against_permit(
                        scene, target_key, binding, permit, {"builtin": b_root}
                    )
                except (PlanError, LibraryError, ValueError, KeyError, TypeError, OSError):
                    continue
                builtin_matches.append(permit)
        if len(builtin_matches) == 1:
            return deepcopy(builtin_matches[0])
        raise ValueError("No committed human-approved or built-in Controller matches this Scene binding")
    except (LibraryError, ValueError, KeyError, TypeError, OSError) as error:
        raise PlanError(
            "deployment_authorization_required",
            "Exact committed human deployment proof is unavailable",
        ) from error


def verify_runtime_deployment(scene, catalog, permit, *, state_root):
    try:
        schema = permit["schema"] if isinstance(permit, dict) and "schema" in permit else None
        if (
            state_root is None
            or schema not in ("lw.deployment-permit/1", "lw.builtin-deployment-permit/1")
            or fingerprint({key: value for key, value in permit.items() if key != "publicationHash"})
            != permit["publicationHash"]
        ):
            raise ValueError("Private deployment descriptor is unavailable or changed")
        b_root = _builtin_root()
        if schema == "lw.deployment-permit/1":
            actual = read_committed_permit(state_root, permit["promotionId"], permit["publicationHash"])
        else:
            actual = _build_builtin_controller_permit(
                permit["controller"]["id"],
                builtin_root=b_root,
                state_root=state_root,
                publication_hash=permit["publicationHash"],
            )
        if actual != permit:
            raise ValueError("Private payload differs from actual committed authority")
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
            if set(materials) != set(material_bindings(catalog)) or not _target_material_matches_candidate(
                target_material, permit
            ):
                raise ValueError("Actual material coverage differs from committed composition")
            controller = permit["controller"]
            document = controller["document"]
            if (
                document["controller"] != controller["id"]
                or document["version"] != controller["version"]
                or document["scene"] != scene.id
            ):
                raise ValueError("Controller document identity differs from the actual Scene")
            roots = (
                {"builtin": b_root}
                if schema == "lw.builtin-deployment-permit/1"
                else _roots_for_scene(scene, state_root)
            )
            for key, identity in materials.items():
                validate_candidate_material(
                    identity,
                    bindings[key],
                    roots,
                    controller=document if key == target else None,
                    allow_entity_remap=(schema == "lw.builtin-deployment-permit/1"),
                )
        else:
            scene_root = getattr(scene, "root", None) if scene is not None else None
            if scene_root == "builtin":
                if schema != "lw.builtin-deployment-permit/1":
                    raise ValueError("Built-in cross-scene deployment requires a built-in permit")
                roots = {"builtin": b_root}
                resolved_scene = _builtin_scene_library(b_root, state_root=state_root).resolve(
                    scene.id, launch=True, source="newton"
                )
            elif scene_root == "user":
                roots = _roots_for_scene(scene, state_root)
                if schema == "lw.builtin-deployment-permit/1" and _controller_superseded_in_user(
                    roots.get("user"),
                    permit["controller"]["id"],
                    permit["controller"]["version"],
                ):
                    raise ValueError("Built-in Reference Controller has been superseded in user library")
                resolved_scene = SceneLibrary(
                    builtin_root=b_root, user_root=roots["user"], state_root=state_root
                ).resolve(scene.id, launch=True, source="newton")
            else:
                raise ValueError("Cross-scene deployment requires a validated Scene")
            if resolved_scene.root != scene_root or resolved_scene.content_hash != scene.content_hash:
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
    except (KeyError, ValueError, TypeError, OSError) as error:
        raise LibraryError(
            "simulation_deployment_changed", "Actual deployed composition is unavailable or differs", 409
        ) from error
    return VerifiedDeployment(deepcopy(permit), deepcopy(bindings))
