"""Program definitions shared by the Runner and gateway; imports no engine."""

from packages.info.catalog import Catalog, load_catalog
from packages.library.common import extra_entity_types
from packages.programs.centrifuge.newton import CentrifugeProgram
from packages.programs.embodied import EmbodiedPolicyProgram, EmbodiedPostureProgram
from packages.programs.linear_axis import LinearAxisProgram
from packages.programs.motorized_incubator import MotorizedIncubatorProgram
from packages.programs.panda_wide import PandaWideProgram
from packages.programs.plate_imager import PlateImagerProgram
from packages.programs.robot_arm.newton import RobotArmProgram
from packages.programs.robot_arm.policy import PolicyProgram
from packages.programs.robot_arm.script import ScriptProgram
from packages.programs.tube.newton import TubeProgram

REGISTRY = {
    program.id: program
    for program in (
        RobotArmProgram,
        PolicyProgram,
        ScriptProgram,
        EmbodiedPostureProgram,
        EmbodiedPolicyProgram,
        TubeProgram,
        CentrifugeProgram,
        PandaWideProgram,
        LinearAxisProgram,
        MotorizedIncubatorProgram,
        PlateImagerProgram,
    )
}


def load_info_catalog(
    manifest, scene_id, *, scene_package=None, policy_subject=None, simulation_subject=None
):
    if scene_package:
        if not scene_package.bindings["entities"]:
            catalog = Catalog(sceneRevision=manifest.sceneRevision)
        else:
            extra = extra_entity_types()
            catalog = load_catalog(
                manifest,
                scene_package.name,
                REGISTRY,
                scene_file=scene_package.path / "devices.yaml",
                template_assets=scene_package.template_assets,
                extra_types=extra or None,
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
