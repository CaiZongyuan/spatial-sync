"""Program definitions shared by the Runner and gateway; imports no engine."""

from packages.info.catalog import Catalog, load_catalog
from packages.programs.centrifuge.newton import CentrifugeProgram
from packages.programs.robot_arm.newton import RobotArmProgram
from packages.programs.tube.newton import TubeProgram

REGISTRY = {program.id: program for program in (RobotArmProgram, TubeProgram, CentrifugeProgram)}


def load_info_catalog(manifest, scene_id, *, scene_package=None):
    if scene_package:
        if not scene_package.bindings["entities"]:
            return Catalog(sceneRevision=manifest.sceneRevision)
        return load_catalog(
            manifest,
            scene_package.name,
            REGISTRY,
            scene_file=scene_package.path / "devices.yaml",
            template_assets=scene_package.template_assets,
        )
    if scene_id == "robot-and-tubes":
        return load_catalog(manifest, scene_id, REGISTRY)
    return Catalog(sceneRevision=manifest.sceneRevision)
