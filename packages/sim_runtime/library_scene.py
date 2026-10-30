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
    calibrations = {}
    if not asset:
        from packages.library.grasp_calibration import grasp_checks

        problems, calibrations = grasp_checks(
            stage, payload.get("structure", {}).get("effectiveBindings", {}).get("newton", []),
            payload["programs"], payload.get("bindings", {"entities": []}), graph,
        )
        if problems:
            raise ValueError("Physical grasp calibration failed: " + "; ".join(problems))
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
            add_taught_sites(result, stage, paths, payload, calibrations=calibrations)
            result.spatial_grasp_calibrations = calibrations
            bindings = payload.get("structure", {}).get("effectiveBindings", {}).get("newton", [])
            pairs = configured_weld_pairs(
                bindings, {str(path): index for path, index in paths["path_body_map"].items()},
                {label: result.shape_body[index] for index, label in enumerate(result.shape_label)
                 if int(result.shape_flags[index]) & int(newton.ShapeFlags.SITE)},
            )
            if pairs:
                if (metadata.get("simulation") or {}).get("solver") != "mujoco":
                    raise ValueError("Configured program welds require the MuJoCo solver")
                from .welds import declare_weld_pairs

                declare_weld_pairs(result, pairs)
            if include_visuals:
                attach_visuals(result, stage, graph, paths)
        else:
            result.add_ground_plane(label="Validation/Ground")
        return result

    simulation = metadata.get("simulation") or {}
    return SceneConfig(builder, solver=simulation.get("solver") or "xpbd",
                       warmup_steps=simulation.get("warmupSteps") or 1)


def configured_weld_pairs(bindings, bodies, sites):
    """Resolve existing arm/centrifuge Config references before model finalization.

    The shared loader already validates their spatialReference metadata and
    materializes Sites. Keep program-specific assembly here, outside HTTP/info.
    """
    pairs = set()
    for binding in bindings:
        config, program = binding["config"], binding["program"]
        try:
            if program in ("robot_arm.newton", "labware.motion"):
                # Contact-mode opening also queries the disabled hand/tube weld.
                parents = [bodies[config["handBody"]]]
                if program == "labware.motion" and config.get("qualifiedPlacement", False):
                    for key, stations in config.get("placementStations", {}).items():
                        child = bodies[key]
                        for station in stations:
                            parent = sites[station]
                            if parent < -1 or child < 0 or parent == child:
                                raise ValueError(
                                    "Placement weld endpoints must be world/body and a distinct body"
                                )
                            pairs.add((parent, child))
            elif program in ("centrifuge.newton", "cell_passage.centrifuge"):
                parents = [sites[key] for key in config["slotSites"]]
            elif program == "cell_passage.workcell":
                parent = bodies[config["pipetteBody"]]
                for tip in config["tips"]:
                    child = bodies[tip["bodyKey"]]
                    if parent < 0 or child < 0 or parent == child:
                        raise ValueError("Configured tip weld endpoints must be distinct imported bodies")
                    pairs.add((parent, child))
                continue
            else:
                continue
            for parent in parents:
                for key in config["tubes"]:
                    child = bodies[key]
                    if parent < 0 or child < 0 or parent == child:
                        raise ValueError("Configured weld endpoints must be distinct imported bodies")
                    pairs.add((parent, child))
        except KeyError as error:
            raise ValueError("Configured weld reference was not imported: " + str(error)) from error
    return sorted(pairs)


def add_taught_sites(builder, stage, paths, payload, *, calibrations=None):
    """Materialize verified Config Sites without altering their authored visual shapes."""
    import newton
    import warp as wp
    from pxr import UsdGeom

    from packages.library.posture import context_references

    requested = set()
    for binding in payload.get("structure", {}).get("effectiveBindings", {}).get("newton", []):
        config = binding["config"]
        fields = payload["programs"][binding["program"]]["fields"]
        requested.update(path for role, path in context_references(fields, config) if role == "site")
        for name, role in fields.items():
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
    physical_tcp = {row["expected"]["tcpSite"]: row["expected"]
                    for row in (calibrations or {}).values()}
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
        if key in physical_tcp:
            owner = physical_tcp[key]["siteOwner"]
            if owner not in bodies or bodies[owner] != body:
                raise ValueError("Physical TCP imported body owner differs from calibrated USD")
            # Compute local pose in USD doubles, then round once into the model.
            # World-space float32 cancellation must not redefine calibration.
            local, reset = cache.ComputeRelativeTransform(prim, stage.GetPrimAtPath(owner))
            if reset:
                raise ValueError("Physical TCP resets its calibrated owner transform")
            rotation = local.ExtractRotationQuat()
            pose = wp.transform(tuple(local.ExtractTranslation()),
                                wp.quat(*rotation.GetImaginary(), rotation.GetReal()))
        elif body >= 0:
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
    dressing_assets = set()
    warnings = list(getattr(builder, "spatial_warnings", ()))
    for prim in stage.Traverse():
        key = str(prim.GetPath())
        if not (key.startswith("/World/Lab/") or key.startswith("/World/Dressing/")):
            continue
        owners = [layers[spec.layer.identifier] for spec in prim.GetPrimStack()
                  if spec.layer.identifier in layers and str(spec.path) == "/Asset"]
        cube = (
            not owners and key.startswith("/World/Dressing/") and prim.IsA(UsdGeom.Cube)
            and UsdGeom.Imageable(prim).ComputeVisibility() != UsdGeom.Tokens.invisible
            and not any(prefix in dressing_assets for prefix in prim.GetPath().GetPrefixes())
        )
        if not owners and not cube:
            continue
        transform = Gf.Transform(cache.GetLocalToWorldTransform(prim))
        rotation = transform.GetRotation().GetQuat()
        world = wp.transform(tuple(transform.GetTranslation()),
                             wp.quat(*rotation.GetImaginary(), rotation.GetReal()))
        scale = list(map(float, transform.GetScale()))
        if cube:
            size = float(UsdGeom.Cube(prim).GetSizeAttr().Get())
            dressing.append({
                "entityKey": key, "kind": "static", "name": prim.GetName(),
                "initialPose": {"p": list(map(float, world[:3])), "q": list(map(float, world[3:]))},
                "visuals": [{"kind": "primitive", "geometry": {
                    "type": "box", "halfExtents": [abs(value) * size / 2 for value in scale],
                }}], "colliders": [],
            })
            continue
        # The strongest asset root describes this composed placement.
        asset = owners[0]
        filename = asset["metadata"].get("visual")
        if not filename:
            warnings.append({"code": "asset_fallback", "entityKey": key,
                             "message": "Referenced asset has no provided PBR GLB: " + asset["id"]})
        visual = {"kind": "glb", "url": visual_url(asset, filename), "assetUpAxis": "Z",
                  "scale": scale, "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}} if filename else None
        if key.startswith("/World/Dressing/"):
            dressing_assets.add(prim.GetPath())
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
