"""Inspect registered Python builders through their shared pure authoring inputs."""

from pathlib import Path
from types import SimpleNamespace

from packages.protocol.internal import SessionOptions


def resources(metadata, package, bindings):
    builder = metadata["stage"].removeprefix("python:")
    if builder == "lab-demo":
        from packages.sim_runtime.synthetic_geometry import tube_height
        from packages.sim_runtime.synthetic_lab import build_manifest

        manifest = build_manifest(
            "structural-inspection",
            SessionOptions(device="cpu"),
            SimpleNamespace(
                path=Path(package["path"]),
                metadata=metadata,
                bindings=bindings,
            ),
        )
        return {
            "entities": [node.entityKey for node in manifest.nodes],
            "bodies": [node.entityKey for node in manifest.nodes if node.kind == "body"],
            "sites": [site.entityKey for site in manifest.sites],
            "joints": {joint.entityKey: {"kind": joint.type, "limits": None} for joint in manifest.joints},
            "tubeHeights": {key: tube_height(manifest, key)
                            for binding in bindings["entities"]
                            if binding.get("program") == "robot_arm.virtual"
                            and binding.get("config", {}).get("graspHeightM")
                            for key in binding.get("config", {}).get("tubes", [])},
        }
    if builder == "robot-and-tubes":
        from packages.sim_runtime.robot_definition import structure

        return structure()
    if builder == "falling-boxes":
        from packages.sim_runtime.scenes import FALLING_LABELS

        return FALLING_LABELS
    if builder == "usd-lab":
        from packages.sim_runtime.usd_scene import SCENE_PATH

        return {"usdPath": str(SCENE_PATH)}
    raise ValueError("Registered Python builder has no structural resource definition")
