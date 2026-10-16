"""Trusted scene construction helpers; HTTP callers use registered scene IDs."""

import warnings
from pathlib import Path


def import_robot(builder, path: Path, *, prefix: str):
    import newton

    child = newton.ModelBuilder(up_axis=newton.Axis.Z)
    if path.suffix.lower() == ".urdf":
        child.add_urdf(str(path))
    elif path.suffix.lower() in (".xml", ".mjcf"):
        child.add_mjcf(str(path))
    else:
        raise ValueError(f"Unsupported robot format: {path.suffix}")
    builder.add_builder(child, label_prefix=prefix)


def import_usd(builder, path: Path):
    """Apply stage axis conversion once and make labels independent of importer naming."""
    import newton
    from pxr import Usd, UsdGeom, UsdPhysics

    if builder.up_axis != newton.Axis.Z:
        raise ValueError("USD protocol imports require a Z-up builder")
    if not path.is_file():
        raise FileNotFoundError(path)
    stage = Usd.Stage.Open(str(path))
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        # True adopts the stage's axis in Newton 1.7; False rotates into our Z-up builder.
        paths = builder.add_usd(str(path), apply_up_axis_from_stage=False, hide_collision_shapes=True)
    compatibility = list(getattr(builder, "spatial_warnings", ()))
    for warning in captured:
        compatibility.append({"code": "unsupported_usd_schema", "message": str(warning.message)})
    for kind in ("body", "shape", "joint"):
        labels = getattr(builder, f"{kind}_label")
        for prim_path, index in paths[f"path_{kind}_map"].items():
            labels[index] = str(prim_path)
    imported_bodies = {str(key) for key in paths["path_body_map"]}
    imported_shapes = {str(key) for key in paths["path_shape_map"]}
    for prim in stage.Traverse():
        key = str(prim.GetPath())
        if prim.HasAPI(UsdPhysics.RigidBodyAPI) and key not in imported_bodies:
            compatibility.append(
                {
                    "code": "unsupported_usd_schema",
                    "entityKey": key,
                    "message": "USD rigid body was not imported; inspect its enabled/schema settings",
                }
            )
        elif (
            prim.IsA(UsdGeom.Gprim)
            and key not in imported_shapes
            and prim.GetTypeName() not in {"Mesh", "Cube", "Sphere", "Capsule", "Cylinder", "Cone", "Plane"}
        ):
            compatibility.append(
                {
                    "code": "unsupported_usd_schema",
                    "entityKey": key,
                    "message": f"USD {prim.GetTypeName()} is not exported to browser geometry",
                }
            )
    builder.spatial_warnings = compatibility
    return paths
