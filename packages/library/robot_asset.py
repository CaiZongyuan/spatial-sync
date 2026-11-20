"""Exact composed robot Asset identity and authored coordinate checks."""


def scene_robot_asset(scene, entity, stage, graph):
    target = stage.GetPrimAtPath(entity)
    authored = next((item for item in scene.bindings["entities"] if item.get("key") == entity), {})
    template = authored.get("template")
    referenced = []
    # A direct USD reference must identify the robot actually used by this target.
    for snapshot in scene.runtime_assets.values():
        if not snapshot["id"].startswith("robot/") or (template and template != snapshot["id"]):
            continue
        layer = graph.layer(snapshot, snapshot["metadata"]["stage"])
        if any(
            spec.path.pathString == "/Asset" and spec.layer.identifier == layer.identifier
            for spec in target.GetPrimStack()
        ):
            referenced.append(snapshot)
    return referenced[0] if len(referenced) == 1 else None


def asset_coordinates_match(stage, robot, graph, asset, *, mapped_points=()):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    source = Usd.Stage.Open(graph.layer(asset, asset["metadata"]["stage"]))
    if not source or UsdGeom.GetStageMetersPerUnit(source) != UsdGeom.GetStageMetersPerUnit(stage):
        return False
    source_root, scene_root = Sdf.Path("/Asset"), Sdf.Path(robot)
    source_prim = source.GetPrimAtPath(source_root)
    if not source_prim:
        return False
    poses = UsdGeom.XformCache()
    placement = poses.GetLocalToWorldTransform(stage.GetPrimAtPath(scene_root))
    if placement.GetDeterminant3() <= 0 or not Gf.IsClose(placement, placement.RemoveScaleShear(), 1e-6):
        return False
    # Compare in the asset frame so the Scene may translate and rotate the robot.
    scene_to_asset = placement.GetInverse()
    source_to_asset = poses.GetLocalToWorldTransform(source_prim).GetInverse()
    for path in mapped_points:
        expected = source.GetPrimAtPath(path)
        actual = stage.GetPrimAtPath(Sdf.Path(path).ReplacePrefix(source_root, scene_root))
        if (not expected or not actual or not actual.IsA(UsdGeom.Xformable)
                or actual.GetTypeName() != expected.GetTypeName()
                or any(actual.HasAPI(api) != expected.HasAPI(api)
                       for api in (UsdPhysics.RigidBodyAPI, UsdPhysics.CollisionAPI))
                or not Gf.IsClose(
                    poses.GetLocalToWorldTransform(actual) * scene_to_asset,
                    poses.GetLocalToWorldTransform(expected) * source_to_asset, 1e-6,
                )):
            return False
    joint_coordinates = ("physics:axis", "physics:jointEnabled", "physics:localPos0", "physics:localPos1")
    for expected in Usd.PrimRange(source_prim, Usd.TraverseInstanceProxies()):
        if expected.IsA(UsdPhysics.Joint):
            actual = stage.GetPrimAtPath(expected.GetPath().ReplacePrefix(source_root, scene_root))
            if not actual or actual.GetTypeName() != expected.GetTypeName():
                return False
            if any(actual.GetAttribute(key).Get() != expected.GetAttribute(key).Get()
                   for key in joint_coordinates if expected.HasAttribute(key)):
                return False
            for key in ("physics:localRot0", "physics:localRot1"):
                rotation = expected.GetAttribute(key).Get()
                options = (rotation, -rotation) if rotation is not None else (None,)
                if actual.GetAttribute(key).Get() not in options:
                    return False
            for key in ("physics:body0", "physics:body1"):
                targets = expected.GetRelationship(key).GetForwardedTargets()
                mapped = [target.ReplacePrefix(source_root, scene_root) for target in targets]
                if actual.GetRelationship(key).GetForwardedTargets() != mapped:
                    return False
        elif expected.HasAPI(UsdPhysics.RigidBodyAPI):
            actual = stage.GetPrimAtPath(expected.GetPath().ReplacePrefix(source_root, scene_root))
            if not actual or not actual.HasAPI(UsdPhysics.RigidBodyAPI):
                return False
            if not Gf.IsClose(
                poses.GetLocalToWorldTransform(actual) * scene_to_asset,
                poses.GetLocalToWorldTransform(expected) * source_to_asset, 1e-6,
            ):
                return False
    return True
