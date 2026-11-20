"""Compose pinned USD rigid and static collision geometry into mjlab's replicated model."""

import math
import re
import xml.etree.ElementTree as ET

import mujoco
from mjlab.actuator import XmlActuatorCfg
from mjlab.entity import EntityArticulationInfoCfg, EntityCfg
from pxr import Sdf, Usd, UsdGeom

if __package__:
    from apps.node import usd_mjcf as usd
else:
    import lw_usd_mjcf as usd


def entity_config(derived):
    spec = derived.to_mjspec()
    root = spec.body(derived.root_body)
    root.pos = (0.0, 0.0, 0.0)
    root.quat = (1.0, 0.0, 0.0, 0.0)
    actuators = (XmlActuatorCfg(target_names_expr=(r".*",)),) if spec.actuators else ()
    return EntityCfg(
        init_state=EntityCfg.InitialStateCfg(
            pos=derived.root_pos,
            rot=derived.root_quat,
            joint_pos={
                re.escape(joint.name): (
                    joint.initial_position if joint.initial_position is not None else joint.default_position
                )
                for joint in derived.joints
            },
            joint_vel={".*": 0.0} if derived.joints else {},
        ),
        spec_fn=lambda: spec.copy(),
        articulation=EntityArticulationInfoCfg(actuators=actuators),
        collisions=(),
    )


def baked_collision_layout(stage, robot, robot_derived):
    """Retain the legacy single-plane path and compose full USD layouts in the root spec."""
    root = stage.GetPrimAtPath("/World/Lab")
    scope = root.GetPath()
    prims = list(Usd.PrimRange(root, Usd.TraverseInstanceProxies()))
    static = [
        prim
        for prim in prims
        if usd._enabled_collider(prim) and usd._enclosing_rigid_body(stage, prim.GetPath(), scope) is None
    ]
    other_bodies = [
        prim for prim in prims if usd._is_rigid_body(prim) and not prim.GetPath().HasPrefix(Sdf.Path(robot))
    ]
    entities = {"robot": entity_config(robot_derived)}
    if (
        not root.GetCustomDataByKey("lwSceneDraft")
        and not other_bodies
        and (len(static) == 1 and static[0].IsA(UsdGeom.Plane))
    ):
        return entities, None, {}

    combined = usd.derive_usd_mjcf(stage, prim_path=str(scope), allow_multiple_roots=True)
    layout = ET.fromstring(combined.mjcf_xml)
    world = layout.find("worldbody")
    body_paths = {name: path for path, name in combined.body_names_by_path.items()}
    names = {path: "robot/" + name for path, name in robot_derived.body_names_by_path.items()}
    props = {}
    for body in world.findall("body"):
        path = body_paths[body.attrib["name"]]
        world.remove(body)
        if path in names:
            continue
        derived = usd.derive_usd_mjcf(stage, prim_path=path, include_static_colliders=False)
        name = f"prop{len(props)}"
        props[path] = (name, not derived.fixed_base)
        entities[name] = entity_config(derived)
        names.update({path: f"{name}/{body}" for path, body in derived.body_names_by_path.items()})
    # The per-Entity specs already carry exclusions internal to each Entity.
    exclusions = []
    for exclusion in layout.findall("./contact/exclude"):
        first, second = (exclusion.attrib[key] for key in ("body1", "body2"))
        a = names[body_paths[first]] if first != "world" else first
        b = names[body_paths[second]] if second != "world" else second
        if a.split("/", 1)[0] != b.split("/", 1)[0]:
            exclusions.append((a, b))
    for element in ("contact", "actuator"):
        if (section := layout.find(element)) is not None:
            layout.remove(section)
    meshes = {geom.attrib["mesh"] for geom in world.findall("geom") if "mesh" in geom.attrib}
    if (assets := layout.find("asset")) is not None:
        for asset in list(assets):
            if asset.attrib.get("name") not in meshes:
                assets.remove(asset)
    baked = mujoco.MjSpec.from_string(ET.tostring(layout, encoding="unicode"))
    transform_cache = UsdGeom.XformCache()
    sites = stage.GetPrimAtPath("/World/Lab/Sites")
    markers = [
        (prim.GetName(), usd._decompose_matrix(transform_cache.GetLocalToWorldTransform(prim), 1.0)[:2])
        for prim in (sites.GetChildren() if sites else ())
        if prim.IsA(UsdGeom.Xformable)
    ]

    def compose(spec):
        spec.attach(baked.copy(), prefix="layout/", frame=spec.worldbody.add_frame())
        for name, (position, rotation) in markers:
            spec.worldbody.add_site(
                name=f"layout/site/{name}", pos=position, quat=rotation, size=(0.005,) * 3,
            )
        spec.option.disableflags |= mujoco.mjtDisableBit.mjDSBL_FILTERPARENT
        for first, second in exclusions:
            spec.add_exclude(bodyname1=first, bodyname2=second)

    return entities, compose, props


def episode_layout(stage, settings, props):
    cache = UsdGeom.XformCache()

    def marker(path, group, field):
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsA(UsdGeom.Xformable) or not path.startswith(f"/World/Lab/{group}/"):
            raise ValueError(f"{field}: {path} must reference a baked {group} marker")
        transform = cache.GetLocalToWorldTransform(prim)
        forward = transform.TransformDir((1.0, 0.0, 0.0))
        return tuple(transform.ExtractTranslation()), math.atan2(forward[1], forward[0])

    resets = {}
    for index, reset in enumerate(settings.get("rigidReset", ())):
        if reset["key"] not in props or not props[reset["key"]][1]:
            raise ValueError(f"rigidReset[{index}].key: need a floating Scene rigid body")
        resets[reset["key"]] = reset
    targets = [
        marker(path, "Sites", f"targetSites[{index}]")[0]
        for index, path in enumerate(settings.get("targetSites", ()))
    ]
    levels = []
    if curriculum := settings.get("curriculum"):
        for index, level in enumerate(curriculum["levels"]):
            levels.append(
                (
                    marker(level["spawn"], "Waypoints", f"curriculum.levels[{index}].spawn"),
                    marker(level["target"], "Sites", f"curriculum.levels[{index}].target")[0],
                )
            )
    return {"props": props, "resets": resets, "targets": targets, "levels": levels}
