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


def import_usd(builder, path, *, root_path="/", xform=None, hide_collision_shapes=True):
    """Apply stage axis conversion once and make labels independent of importer naming."""
    import newton
    from newton.usd import SchemaResolverMjc, SchemaResolverNewton, SchemaResolverPhysx
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    from apps.node.usd_mjcf import bound_contact_material, filtered_collider_pairs, joint_armature

    from .usd_articulations import root_import

    if builder.up_axis != newton.Axis.Z:
        raise ValueError("USD protocol imports require a Z-up builder")
    if isinstance(path, (str, Path)):
        if not Path(path).is_file():
            raise FileNotFoundError(path)
        stage = Usd.Stage.Open(str(path))
    else:
        stage = path
    resolvers = [SchemaResolverNewton(), SchemaResolverPhysx()]
    if any(attr.namespace == "mujoco" for attr in builder.custom_attributes.values()):
        resolvers.append(SchemaResolverMjc())
    arguments = {"root_path": root_path, "apply_up_axis_from_stage": False,
                 "hide_collision_shapes": hide_collision_shapes,
                 "schema_resolvers": resolvers}
    if xform is not None:
        arguments["xform"] = xform
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        # True adopts the stage's axis in Newton 1.7; False rotates into our Z-up builder.
        with root_import(builder, stage, root_path) as articulations:
            paths = builder.add_usd(stage, **arguments)
    compatibility = list(getattr(builder, "spatial_warnings", ()))
    for warning in captured:
        compatibility.append({"code": "unsupported_usd_schema", "message": str(warning.message)})
    for kind in ("body", "shape", "joint"):
        labels = getattr(builder, f"{kind}_label")
        for prim_path, index in paths[f"path_{kind}_map"].items():
            labels[index] = str(prim_path)
    for joint_path, index in paths["path_joint_map"].items():
        if builder.joint_type[index] in (newton.JointType.REVOLUTE, newton.JointType.PRISMATIC):
            dof = builder.joint_qd_start[index]
            builder.joint_armature[dof] = joint_armature(
                stage.GetPrimAtPath(joint_path), builder.joint_armature[dof]
            )

    # The native reader covers individual bodies and shapes. Include authored
    # filters with containing scopes as endpoints once real shape IDs are known.
    shapes = {Sdf.Path(str(path)): index for path, index in paths["path_shape_map"].items()
              if builder.shape_flags[index] & newton.ShapeFlags.COLLIDE_SHAPES}
    for collider_path, index in shapes.items():
        material = bound_contact_material(stage.GetPrimAtPath(collider_path))
        if material is not None:
            builder.shape_material_mu[index], builder.shape_material_restitution[index] = material
    prims = list(stage.Traverse(Usd.TraverseInstanceProxies()))
    if any(prim.HasRelationship("physics:filteredPairs") for prim in prims):
        body_paths = {index: Sdf.Path(str(path)) for path, index in paths["path_body_map"].items()}
        owners = {path: body_paths.get(builder.shape_body[index], Sdf.Path.absoluteRootPath)
                  for path, index in shapes.items()}
        existing = set(builder.shape_collision_filter_pairs)
        for first, second in sorted(filtered_collider_pairs(prims, owners)):
            pair = tuple(sorted((shapes[first], shapes[second])))
            if pair not in existing:
                builder.add_shape_collision_filter_pair(*pair)
                existing.add(pair)
    compatibility.extend(
        {"code": "usd_articulation_root_compatibility", "entityKey": row["label"],
         "message": "Recovered explicit single-body articulation membership omitted by the pinned USD reader"}
        for row in articulations.recovered
    )
    imported_bodies = {str(key) for key in paths["path_body_map"]}
    imported_shapes = {str(key) for key in paths["path_shape_map"]}
    prims = stage.Traverse() if root_path == "/" else Usd.PrimRange(stage.GetPrimAtPath(root_path))
    for prim in prims:
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
