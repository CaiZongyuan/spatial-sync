"""One verified USD construction path for the CPU validator and the live Runner."""

import math
from fnmatch import fnmatchcase

from .scenes import SceneConfig


def prepare_scene_config(payload, *, include_visuals=False):
    from packages.library.templates import require_spatial_domain

    if payload["kind"] == "scene":
        for binding in payload.get("structure", {}).get("effectiveBindings", {}).get("newton", []):
            require_spatial_domain(binding["config"], payload["programs"][binding["program"]]["fields"],
                                   "/World/Lab")
    # Only the worker/Runner calls this function; importing the module is SDK-free.
    import newton
    import warp as wp
    from pxr import UsdGeom, UsdPhysics

    from packages.library.structural import USDGraph

    from .loaders import import_usd

    metadata = payload["metadata"]
    graph = USDGraph(payload["package"], payload["assets"], payload.get("expectedAssets", []))
    stage = graph.compose(metadata["stage"])
    if stage is None or graph.errors:
        raise ValueError("USD dependencies could not be composed: " + "; ".join(graph.errors))
    asset = payload["kind"] == "asset"
    root = "/Asset" if asset else "/World/Lab"
    if not stage.GetPrimAtPath(root):
        raise ValueError("USD physics root is missing: " + root)
    offset = 0.
    if asset:
        cache = UsdGeom.BBoxCache(0, ["default", "render", "proxy"])
        bottoms = [float(cache.ComputeWorldBound(prim).ComputeAlignedRange().GetMin()[2])
                   for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.CollisionAPI)
                   and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False]
        if bottoms:
            offset = -min(bottoms)
            if not math.isfinite(offset):
                raise ValueError("Asset collision bounds are not finite")

    def builder():
        result = newton.ModelBuilder(up_axis=newton.Axis.Z)
        if (metadata.get("simulation") or {}).get("solver") == "mujoco":
            from newton.solvers import SolverMuJoCo

            SolverMuJoCo.register_custom_attributes(result)
        result.spatial_dependency_files = tuple(graph.clones)
        paths = import_usd(result, stage, root_path=root, hide_collision_shapes=False,
                           xform=wp.transform((0, 0, offset), wp.quat_identity()))
        if not asset:
            add_taught_sites(result, stage, paths, payload)
            if include_visuals:
                attach_visuals(result, stage, graph, paths)
        else:
            result.add_ground_plane(label="Validation/Ground")
        return result

    simulation = metadata.get("simulation") or {}
    return SceneConfig(builder, solver=simulation.get("solver") or "xpbd",
                       warmup_steps=simulation.get("warmupSteps") or 1)


def add_taught_sites(builder, stage, paths, payload):
    """Materialize verified Config Sites without altering their authored visual shapes."""
    import newton
    import warp as wp
    from pxr import UsdGeom

    requested = set()
    for binding in payload.get("structure", {}).get("effectiveBindings", {}).get("newton", []):
        config = binding["config"]
        for name, role in payload["programs"][binding["program"]]["fields"].items():
            value = config.get(name)
            if role.get("spatialReference") == "site":
                requested.update([value] if isinstance(value, str) else value or [])
            if role.get("documentSchema") == "locations" and isinstance(value, dict):
                requested.update(location["site"] for location in value["locations"].values()
                                 if location.get("site"))
    expanded = set()
    available = None
    for pattern in requested:
        if "*" in pattern:
            from packages.library.structural import usd_resources

            if available is None:
                available = usd_resources(stage)["sites"]
            matches = [key for key in available if fnmatchcase(key, pattern)]
        else:
            matches = [pattern]
        if not matches:
            raise ValueError("Configured Site pattern matched no USD Sites: " + pattern)
        expanded.update(matches)
    requested = expanded
    shape_paths = {str(path): index for path, index in paths["path_shape_map"].items()}
    sites = {path for path, index in shape_paths.items()
             if int(builder.shape_flags[index]) & int(newton.ShapeFlags.SITE)}
    bodies = {str(path): index for path, index in paths["path_body_map"].items()}
    cache = UsdGeom.XformCache()
    added = {}
    for key in sorted(requested - sites):
        prim = stage.GetPrimAtPath(key)
        if not prim or not prim.IsA(UsdGeom.Xformable):
            raise ValueError("Configured Site is not an authored USD transform: " + key)
        world = cache.GetLocalToWorldTransform(prim)
        quaternion = world.ExtractRotationQuat()
        pose = wp.transform(tuple(world.ExtractTranslation()),
                            wp.quat(*quaternion.GetImaginary(), quaternion.GetReal()))
        owners = [path for path in bodies if key.startswith(path + "/")]
        body = bodies[max(owners, key=len)] if owners else -1
        if body >= 0:
            pose = wp.transform_multiply(wp.transform_inverse(builder.body_q[body]), pose)
        site = builder.add_site(body, xform=pose, label=key)
        original = shape_paths.get(key)
        if original is not None:
            added[site] = original
    # These aliases are produced only for the exact imported USD prim above.
    builder.spatial_site_visuals = added


def attach_visuals(builder, stage, graph, paths):
    """Bind a provided asset visual to its actual rigid body or nonphysical dressing."""
    import warp as wp
    from pxr import Gf, UsdGeom

    from packages.library.scenes import visual_url

    layers = {graph.clones[str(path)].identifier: asset
              for asset in graph.assets.values() for filename, path in asset["files"].items()
              if filename == asset["metadata"]["stage"] and str(path) in graph.clones}
    cache = UsdGeom.XformCache()
    bodies = {str(path): index for path, index in paths["path_body_map"].items()}
    shape_paths = {str(path): index for path, index in paths["path_shape_map"].items()}
    visuals, static_visuals, dressing = {}, {}, []
    warnings = list(getattr(builder, "spatial_warnings", ()))
    for prim in stage.Traverse():
        key = str(prim.GetPath())
        if not (key.startswith("/World/Lab/") or key.startswith("/World/Dressing/")):
            continue
        owners = [layers[spec.layer.identifier] for spec in prim.GetPrimStack()
                  if spec.layer.identifier in layers and str(spec.path) == "/Asset"]
        if not owners:
            continue
        # The strongest asset root describes this composed placement.
        asset = owners[0]
        filename = asset["metadata"].get("visual")
        transform = Gf.Transform(cache.GetLocalToWorldTransform(prim))
        rotation = transform.GetRotation().GetQuat()
        world = wp.transform(tuple(transform.GetTranslation()),
                             wp.quat(*rotation.GetImaginary(), rotation.GetReal()))
        scale = list(map(float, transform.GetScale()))
        if not filename:
            warnings.append({"code": "asset_fallback", "entityKey": key,
                             "message": "Referenced asset has no provided PBR GLB: " + asset["id"]})
        visual = {"kind": "glb", "url": visual_url(asset, filename), "assetUpAxis": "Z",
                  "scale": scale, "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}} if filename else None
        if key.startswith("/World/Dressing/"):
            dressing.append({"entityKey": key, "kind": "static", "name": prim.GetName(),
                             "initialPose": {"p": list(map(float, world[:3])),
                                             "q": list(map(float, world[3:]))},
                             "visuals": [visual] if visual else [], "colliders": []})
        elif visual:
            if key not in bodies:
                shape = shape_paths.get(key)
                if shape is not None and builder.shape_body[shape] == -1:
                    local = wp.transform_multiply(wp.transform_inverse(builder.shape_transform[shape]), world)
                    visual["localPose"] = {"p": list(map(float, local[:3])),
                                           "q": list(map(float, local[3:]))}
                    static_visuals[key] = [visual]
                else:
                    warnings.append({"code": "asset_fallback", "entityKey": key,
                                     "message": "Ambiguous GLB owner; retaining native visuals"})
                continue
            body = bodies[key]
            local = wp.transform_multiply(wp.transform_inverse(builder.body_q[body]), world)
            visual["localPose"] = {"p": list(map(float, local[:3])), "q": list(map(float, local[3:]))}
            visuals.setdefault(body, []).append(visual)
    builder.spatial_visuals = visuals
    builder.spatial_static_visuals = static_visuals
    builder.spatial_dressing = dressing
    builder.spatial_warnings = warnings
