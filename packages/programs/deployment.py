"""Rebuild committed authority, then verify the actual Runner Catalog and each artifact."""

from copy import deepcopy
from dataclasses import dataclass

from packages.library.assets import AssetLibrary
from packages.library.common import LibraryError
from packages.programs.simulation import material_bindings, validate_candidate_material
from packages.programs.subjects import fingerprint
from packages.training.deployment_authority import read_committed_permit


@dataclass(frozen=True)
class VerifiedDeployment:
    permit: dict
    bindings: dict


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
        if permit["scene"] != {"id": scene.id, "contentHash": scene.content_hash} or permit[
            "appliedBindings"
        ] != {key: fingerprint(binding) for key, binding in bindings.items()}:
            raise ValueError("Actual Scene or complete binding composition differs")
        target = permit["target"]["entityKey"]
        if (
            permit["binding"] != bindings.get(target)
            or fingerprint(permit["binding"]) != permit["bindingHash"]
        ):
            raise ValueError("Actual deployed target binding differs")
        materials = permit["materialIdentities"]
        if (
            set(materials) != set(material_bindings(catalog))
            or materials.get(target) != permit["candidateIdentity"]
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
        roots = AssetLibrary(state_root).roots
        for key, identity in materials.items():
            validate_candidate_material(
                identity, bindings[key], roots, controller=document if key == target else None
            )
    except (KeyError, ValueError, TypeError) as error:
        raise LibraryError(
            "simulation_deployment_changed", "Actual deployed composition is unavailable or differs", 409
        ) from error
    return VerifiedDeployment(deepcopy(permit), deepcopy(bindings))
