"""Program definitions shared by the Runner and gateway; imports no engine."""

from packages.info.catalog import Catalog, load_catalog
from packages.programs.centrifuge.newton import CentrifugeProgram
from packages.programs.robot_arm.newton import RobotArmProgram
from packages.programs.robot_arm.policy import PolicyProgram
from packages.programs.robot_arm.script import ScriptProgram
from packages.programs.tube.newton import TubeProgram

REGISTRY = {
    program.id: program
    for program in (RobotArmProgram, PolicyProgram, ScriptProgram, TubeProgram, CentrifugeProgram)
}


def load_info_catalog(
    manifest, scene_id, *, scene_package=None, policy_subject=None, simulation_subject=None
):
    if scene_package:
        if not scene_package.bindings["entities"]:
            catalog = Catalog(sceneRevision=manifest.sceneRevision)
        else:
            catalog = load_catalog(
                manifest,
                scene_package.name,
                REGISTRY,
                scene_file=scene_package.path / "devices.yaml",
                template_assets=scene_package.template_assets,
            )
    elif scene_id == "robot-and-tubes":
        catalog = load_catalog(manifest, scene_id, REGISTRY)
    else:
        catalog = Catalog(sceneRevision=manifest.sceneRevision)
    from packages.programs.simulation import apply_simulation_catalog, select_subject

    subject = select_subject(simulation_subject=simulation_subject, policy_subject=policy_subject)
    if subject is not None:
        if manifest.source.kind != "newton":
            raise ValueError("Policy overrides require the actual Newton Source")
        apply_simulation_catalog(catalog, subject, scene_package)
    return catalog
