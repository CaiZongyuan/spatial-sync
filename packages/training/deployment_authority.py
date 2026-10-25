"""Pure committed deployment authority shared by Gateway and native Runner."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from packages.info.approvals import ApprovalRecord
from packages.info.plans import PlanError
from packages.library.archive import destination_parent
from packages.library.common import BUILTIN_ROOT, NAME, LibraryError, document
from packages.library.scenes import SceneLibrary
from packages.policies.library import PolicyLibrary
from packages.policies.subjects import fixed_bindings
from packages.programs.subjects import fingerprint


def _private_document(path):
    if (
        not path.is_file()
        or path.is_symlink()
        or path.is_junction()
        or path.stat().st_nlink != 1
        or path.stat().st_size > 4 * 1024 * 1024
    ):
        raise ValueError("Deployment authority is not a regular bounded document")
    return document(path)


def read_committed_permit(state_root, promotion_id, publication_hash=None):
    """Pure metadata read for Gateway and Runner; neither a wire bool nor a Plan authorizes deployment."""
    try:
        if not isinstance(promotion_id, str) or not NAME.fullmatch(promotion_id):
            raise ValueError("Invalid deployment identity")
        state = Path(state_root)
        root = destination_parent(state, ("controller-promotions",))
        promotion = _private_document(root / f"{promotion_id}.json")
        directory = destination_parent(root, ("controller-publications", promotion_id))
        publication = _private_document(directory / "record.json")
        context = promotion["context"]
        context_hash = fingerprint(context)
        intent = publication["intent"]
        if (
            promotion["schema"] != "lw.controller-promotion/1"
            or publication["schema"] != "lw.controller-publication/1"
            or promotion["id"] != promotion_id
            or publication["id"] != promotion_id
            or promotion["status"] not in ("approved", "publishing", "published")
            or publication["phase"] != "committed"
            or promotion["contextHash"] != context_hash
            or intent["contextHash"] != context_hash
            or publication["intentHash"] != fingerprint(intent)
            or any(
                context[key] != intent[key]
                for key in ("baseScene", "subject", "controller", "versionFields", "libraryRoot")
            )
        ):
            raise ValueError("Deployment has no matching committed frozen context")
        audit = state / "approvals.jsonl"
        if not audit.is_file() or audit.is_symlink() or audit.is_junction() or audit.stat().st_nlink != 1:
            raise ValueError("Deployment human audit is unavailable")
        approval = None
        with audit.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    break  # A partial decision never establishes authority.
                value = ApprovalRecord.model_validate_json(line)
                if value.approvalId == promotion["approvalId"]:
                    approval = value
        if (
            approval is None
            or approval.subject != "policy"
            or approval.subjectId != promotion_id
            or approval.status != "approved"
            or not approval.decisions
            or approval.decisions[-1].decision != "approve"
            or approval.decisions[-1].actor.kind != "user"
            or approval.details.get("controllerPromotionId") != promotion_id
            or approval.details.get("contextHash") != context_hash
        ):
            raise ValueError("Deployment has no exact human approval")
        library = Path(intent["libraryRoot"])
        scenes = SceneLibrary(user_root=library, state_root=state)
        subject = intent["subject"]
        source = subject["evaluationScene"]["source"]
        scene = scenes.resolve(publication["scene"]["id"], launch=True, source=source)
        lineage = intent["baseScene"] | {"promotion": promotion_id}
        if (
            scene.root != "user"
            or scene.content_hash != publication["scene"]["contentHash"]
            or scene.metadata.get("derivedFrom") != lineage
        ):
            raise ValueError("Actual deployed Scene differs from its committed snapshot")
        applied = dict(subject["baseBindings"])
        applied[subject["target"]["entityKey"]] = subject["bindingHash"]
        actual = {key: fingerprint(value) for key, value in fixed_bindings(scene, source).items()}
        if applied != actual or fingerprint(subject["binding"]) != subject["bindingHash"]:
            raise ValueError("Actual deployed bindings differ from human-reviewed composition")
        controller = publication["controller"]["document"]
        directory = destination_parent(library, ("controllers", controller["controller"]))
        path = directory / f"{controller['version']}.yaml"
        actual_controller = _private_document(path)
        if (
            sha256(path.read_bytes()).hexdigest() != publication["controller"]["contentHash"]
            or actual_controller != controller
            or controller["scene"] != scene.id
            or controller["program"] != subject["binding"]["program"]
            or controller["controller"] != intent["controller"]["id"]
            or controller["embodiment"] != intent["controller"]["embodiment"]
            or controller["version"] != publication["controllerVersion"]
            or publication["newCurrent"]["id"] != controller["controller"]
            or publication["newCurrent"]["currentVersion"] != controller["version"]
        ):
            raise ValueError("Actual Controller snapshot differs from committed composition")
        candidate = subject["candidateIdentity"]
        materials = context["materialIdentities"]
        if materials.get(subject["target"]["entityKey"]) != candidate:
            raise ValueError("Committed target material identity differs")
        bindings = fixed_bindings(scene, source)
        actual_material_keys = {
            key
            for key, value in bindings.items()
            if value["program"] in ("robot_arm.policy", "robot_arm.script")
        }
        if set(materials) != actual_material_keys:
            raise ValueError("Committed material identity coverage differs from the actual Scene")
        roots = {"builtin": BUILTIN_ROOT, "user": library}
        for key, identity in materials.items():
            config = bindings[key]["config"]
            if identity["kind"] == "policy":
                material, _ = PolicyLibrary(roots).resolve(identity["id"])
                requested = config.get("policy")
            else:
                from packages.programs.library import ProgramLibrary

                material, _ = ProgramLibrary(roots).resolve(identity["id"])
                requested = (config.get("programArtifact") or {}).get("id")
                if config.get("programArtifact") != {name: identity[name] for name in ("id", "contentHash")}:
                    raise ValueError("Actual script config differs from committed material")
            if requested != identity["id"] or material["contentHash"] != identity["contentHash"]:
                raise ValueError("Actual material changed under its committed immutable reference")
        if candidate["kind"] == "policy":
            if (
                controller["policy"] != candidate["id"]
                or subject["binding"]["config"]["policy"] != candidate["id"]
            ):
                raise ValueError("Actual deployed Policy identity differs")
        else:
            artifact = {key: candidate[key] for key in ("id", "contentHash")}
            if (
                controller["programArtifact"] != artifact
                or subject["binding"]["config"]["programArtifact"] != artifact
            ):
                raise ValueError("Actual deployed script identity differs")
        permit = {
            "schema": "lw.deployment-permit/1",
            "promotionId": promotion_id,
            "approvalId": promotion["approvalId"],
            "contextHash": context_hash,
            "scene": deepcopy(publication["scene"]),
            "controller": {
                "id": controller["controller"],
                "version": controller["version"],
                "contentHash": publication["controller"]["contentHash"],
                "document": controller,
            },
            "candidateIdentity": candidate,
            "materialIdentities": materials,
            "target": subject["target"],
            "binding": subject["binding"],
            "bindingHash": subject["bindingHash"],
            "appliedBindings": applied,
        }
        permit["publicationHash"] = fingerprint(permit)
        if publication_hash is not None and permit["publicationHash"] != publication_hash:
            raise ValueError("Private deployment transport differs from actual committed authority")
        return permit
    except (OSError, ValueError, KeyError, TypeError, LibraryError) as error:
        raise PlanError(
            "deployment_authorization_required", "Exact committed human deployment proof is unavailable"
        ) from error
