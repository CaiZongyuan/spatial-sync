"""Prepare actual FR3 and local primitive tube/rack USD assets kitlessly."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("PXR_WORK_THREAD_LIMIT", "1")


def prepare(repository: Path, output: Path) -> None:
    """Convert FR3 without changing its URDF or the tutorial's SO-101 assets."""
    from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    asset = repository / "assets/robots/franka_emika_panda"
    urdf = asset / "urdf/fr3_franka_hand.urdf"
    output.mkdir(parents=True, exist_ok=True)
    for name in ("Tube.usda", "Rack.usda", "Table.usda"):
        if (output / name).exists():
            raise FileExistsError(f"Use a fresh asset output directory: {output / name}")
    converter = UrdfConverter(
        UrdfConverterCfg(
            asset_path=str(urdf),
            usd_dir=str(output),
            fix_base=True,
            merge_fixed_joints=True,
            self_collision=False,
            ros_package_paths=[{"name": "franka_emika_panda", "path": str(asset)}],
            joint_drive=UrdfConverterCfg.JointDriveCfg(
                gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=600.0, damping=50.0)
            ),
        )
    )

    def stage_for(name: str):
        stage = Usd.Stage.CreateNew(str(output / f"{name}.usda"))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        root = UsdGeom.Xform.Define(stage, f"/{name}").GetPrim()
        stage.SetDefaultPrim(root)
        return stage, root

    def box(stage, path, center, size):
        shape = UsdGeom.Cube.Define(stage, path)
        shape.CreateSizeAttr(1.0)
        shape.AddTranslateOp().Set(Gf.Vec3d(*center))
        shape.AddScaleOp().Set(Gf.Vec3f(*size))
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
        return shape

    stage, root = stage_for("Tube")
    UsdPhysics.RigidBodyAPI.Apply(root)
    mass = UsdPhysics.MassAPI.Apply(root)
    mass.CreateMassAttr(0.005)
    shape = UsdGeom.Cylinder.Define(stage, "/Tube/Glass")
    shape.CreateAxisAttr("Z")
    shape.CreateRadiusAttr(0.008)
    shape.CreateHeightAttr(0.030)
    shape.CreateDisplayColorAttr([Gf.Vec3f(0.15, 0.7, 0.85)])
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    stage.GetRootLayer().Save()

    stage, root = stage_for("Rack")
    rigid = UsdPhysics.RigidBodyAPI.Apply(root)
    rigid.CreateKinematicEnabledAttr(True)
    box(stage, "/Rack/Support", (0, 0, -0.006), (0.024, 0.024, 0.012))
    for side in (-1, 1):
        box(stage, f"/Rack/Guide{side + 1}", (side * 0.014, 0, 0.008), (0.004, 0.024, 0.028))
    stage.GetRootLayer().Save()

    stage, root = stage_for("Table")
    box(stage, "/Table/Top", (0.4, 0.0, 0.0), (0.6, 0.7, 0.04))
    stage.GetRootLayer().Save()

    manifest = {
        "robot": "FR3 (repository URDF, not Panda)",
        "urdf_sha256": hashlib.sha256(urdf.read_bytes()).hexdigest(),
        "robot_usd": converter.usd_path,
        "tube": {"radius_m": 0.008, "length_m": 0.03, "mass_kg": 0.005},
        "rack": {"opening_x_m": 0.024, "rim_z_m": 0.022},
        "conventions": {"up": "Z", "quaternion": "XYZW", "tube_origin": "center"},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.repository.resolve(), args.output.resolve())
