"""Deterministic Asset-local retargeting and sampled physical feasibility checks."""

import hashlib
import math
from itertools import product

from packages.info.plans import PlanError
from packages.library.common import content_hash

LIMBS = {
    "leftLeg": ("leftHip", "leftKnee", "leftAnkle"),
    "rightLeg": ("rightHip", "rightKnee", "rightAnkle"),
    "leftArm": ("leftShoulder", "leftElbow", "leftWrist"),
    "rightArm": ("rightShoulder", "rightElbow", "rightWrist"),
}
TIPS = {"leftLeg": "leftFoot", "rightLeg": "rightFoot", "leftArm": "leftHand", "rightArm": "rightHand"}
FLOOR_TOLERANCE = 0.004
SLIP_TOLERANCE = 0.02
KEYFRAME_DURATION = 30


def reject(code, message, **details):
    raise PlanError(code, message, status=422, **details)


def build_reference(asset, request, clip):
    import mujoco
    import numpy as np
    from scipy.optimize import least_squares
    from scipy.spatial import ConvexHull, QhullError
    from scipy.spatial.transform import Rotation

    from apps.node.usd_mjcf import derive_usd_mjcf
    from packages.policies.motion import motion_observations

    if asset.id != request.asset or asset.content_hash != request.assetContentHash:
        reject("reference_asset_changed", "Reference Motion requires the exact calibrated Asset version")
    if content_hash(asset.path) != asset.content_hash:
        reject("reference_asset_changed", "Target Asset bytes changed")
    stage = asset.path / asset.metadata["stage"]
    derived = derive_usd_mjcf(stage, prim_path="/Asset", include_ground_plane=True)
    model = derived.to_mjmodel()
    data = mujoco.MjData(model)
    root = model.body(derived.root_body).id
    keys = [joint.prim_path for joint in derived.joints if joint.actuated]
    joints = {joint.prim_path: joint for joint in derived.joints if joint.actuated}
    if not keys or any(not joint.limited for joint in joints.values()):
        reject("reference_no_actuators", "Target Asset needs actuated joints with authored position limits")
    envelopes = {}
    for key, joint in joints.items():
        lower, upper = joint.target_range or (joint.lower, joint.upper)
        envelopes[key] = max(lower, joint.lower), min(upper, joint.upper)
        if envelopes[key][0] >= envelopes[key][1]:
            reject("reference_target_range", "Actuator and joint position ranges do not overlap", joint=key)
    qpos = {key: int(model.joint(joints[key].name).qposadr[0]) for key in keys}
    nominal = model.qpos0.copy()
    for key, address in qpos.items():
        nominal[address] = joints[key].default_position

    mapping = request.mapping.model_dump(exclude_none=True)
    if abs(math.hypot(*request.mapping.sourceToAsset) - 1) > 1e-6:
        reject("reference_mapping_invalid", "sourceToAsset must be a unit XYZW quaternion")
    rotation = Rotation.from_quat(request.mapping.sourceToAsset)
    targets = {}
    for name in ("pelvis", "trunk", "head", "leftFoot", "rightFoot", "leftHand", "rightHand"):
        path = mapping.get(name)
        if path is None:
            continue
        if path in derived.body_names_by_path:
            targets[name] = ("body", model.body(derived.body_names_by_path[path]).id)
        elif path.rsplit("/", 1)[-1] in derived.sites and path.count("/") >= 3:
            # Verify the full USD owner path, not just a site leaf shared by other bodies.
            site = model.site(path.rsplit("/", 1)[-1]).id
            owner = path.rsplit("/", 1)[0]
            if owner not in derived.body_names_by_path or (
                model.body(derived.body_names_by_path[owner]).id != model.site_bodyid[site]
            ):
                reject("reference_mapping_invalid", "Mapped site is outside its authored body", path=path)
            targets[name] = ("site", site)
        else:
            reject("reference_mapping_invalid", "Mapped body or site was not found", path=path)
    if targets["pelvis"] != ("body", root):
        reject("reference_mapping_invalid", "The pelvis mapping must identify the Asset root body")

    def position(name):
        kind, index = targets[name]
        return data.xpos[index] if kind == "body" else data.site_xpos[index]

    def foot_body(name):
        kind, index = targets[name]
        return index if kind == "body" else int(model.site_bodyid[index])

    (floor,) = np.flatnonzero((model.geom_bodyid == 0) & (model.geom_type == mujoco.mjtGeom.mjGEOM_PLANE))
    collides_with_floor = (model.geom_contype & model.geom_conaffinity[floor]) | (
        model.geom_conaffinity & model.geom_contype[floor]
    )
    feet = {}
    for name in ("leftFoot", "rightFoot"):
        body = foot_body(name)
        meshes = []
        for geom in np.flatnonzero((model.geom_bodyid == body) & (collides_with_floor != 0)):
            if model.geom_type[geom] == mujoco.mjtGeom.mjGEOM_BOX:
                vertices = np.array(list(product((-1, 1), repeat=3))) * model.geom_size[geom]
            elif model.geom_type[geom] == mujoco.mjtGeom.mjGEOM_MESH:
                mesh = model.geom_dataid[geom]
                start = model.mesh_vertadr[mesh]
                vertices = model.mesh_vert[start : start + model.mesh_vertnum[mesh]]
            else:
                reject("reference_support_unsupported", "Foot support requires collision meshes or boxes")
            meshes.append((geom, vertices))
        if not meshes:
            reject("reference_support_unsupported", "Each foot mapping requires physical collision support")
        feet[name] = meshes
    if foot_body("leftFoot") == foot_body("rightFoot"):
        reject("reference_mapping_invalid", "Left and right foot must be distinct rigid bodies")

    def forward(values):
        data.qpos[:] = values
        mujoco.mj_forward(model, data)

    def sole(name):
        return np.concatenate(
            [
                vertices @ data.geom_xmat[index].reshape(3, 3).T + data.geom_xpos[index]
                for index, vertices in feet[name]
            ]
        )

    forward(nominal)
    rest_points = {name: position(name).copy() for name in targets}
    root_position = data.xpos[root].copy()
    root_quat = data.xquat[root].copy()

    def packet(time, values):
        forward(values)
        return {
            "time": time,
            "position": data.xpos[root].tolist(),
            "orientation": data.xquat[root][[1, 2, 3, 0]].tolist(),
            "joints": [float(values[qpos[key]]) for key in keys],
            "bodyLocal": {
                mapping[name]: (data.xmat[root].reshape(3, 3).T @ (position(name) - data.xpos[root])).tolist()
                for name in targets
                if name != "pelvis"
            },
        }

    def to_qpos(frame):
        values = nominal.copy()
        for key, angle in zip(keys, frame["joints"], strict=True):
            values[qpos[key]] = angle
        if derived.fixed_base:
            if (
                np.linalg.norm(np.asarray(frame["position"]) - root_position) > 1e-6
                or abs(np.dot(frame["orientation"], root_quat[[1, 2, 3, 0]])) < 1 - 1e-6
            ):
                reject("reference_fixed_base", "External keyframes move a fixed Asset root")
        else:
            values[:3] = frame["position"]
            values[3:7] = np.asarray(frame["orientation"])[[3, 0, 1, 2]]
        return values

    def coverage(joint_key, name):
        # A declared limb driver must physically influence the mapped endpoint.
        forward(nominal)
        start = position(name).copy()
        displaced = nominal.copy()
        displaced[qpos[joint_key]] += 0.01
        forward(displaced)
        return np.linalg.norm(position(name) - start) > 1e-7

    errors = []
    point_errors = {}
    point_sources = {}
    if not 2 <= len(clip["frames"] if clip is not None else request.keyframes.frames) <= 2047:
        reject("reference_motion_limits", "Reference Motion needs between 2 and 2047 source frames")
    if clip is not None:
        if not request.inPlace:
            reject("reference_root_unobserved", "This Motion Clip has no observed world root translation")
        source_frames = [row for row in clip["frames"] if row["joints"] is not None]
        if len(source_frames) < 2:
            reject("reference_no_motion", "At least two usable source poses are required")
        times = [row["timeSeconds"] - source_frames[0]["timeSeconds"] for row in source_frames]
        landmarks = [
            {key: np.asarray(point["position"]) for key, point in row["joints"].items()}
            for row in source_frames
        ]

        def skeleton(frame):
            pelvis = (frame["leftHip"] + frame["rightHip"]) / 2
            trunk = (frame["leftShoulder"] + frame["rightShoulder"]) / 2
            return {
                name: rotation.apply(point - pelvis)
                for name, point in {
                    "pelvis": pelvis,
                    "trunk": trunk,
                    "leftHand": frame["leftWrist"],
                    "rightHand": frame["rightWrist"],
                }.items()
            }

        source_points = [skeleton(frame) for frame in landmarks]
        scale = np.linalg.norm(rest_points["leftFoot"] - root_position) / max(
            np.linalg.norm(landmarks[0]["leftAnkle"] - landmarks[0]["leftHip"]), 0.001
        )
        soft_points = [
            name
            for name in source_points[0]
            if name in targets
            and (name not in ("pelvis", "trunk") or (name == "trunk" and targets[name] != targets["pelvis"]))
        ]
        signals, activity, mixed = {}, {}, {}
        for limb, (first, middle, last) in LIMBS.items():
            vectors = np.array(
                [[frame[middle] - frame[first], frame[last] - frame[middle]] for frame in landmarks]
            )
            lengths = np.linalg.norm(vectors, axis=2)
            if np.any(lengths < 0.001):
                reject("reference_degenerate_limb", "Source limb landmarks collapse", limb=limb)
            directions = vectors / lengths[:, :, None]
            angles = np.arccos(np.clip(-np.sum(directions[:, 0] * directions[:, 1], axis=1), -1, 1))
            swings = np.arccos(np.clip(np.sum(directions * directions[0], axis=2), -1, 1))
            bend = float(np.ptp(angles))
            # Proximal swing and joint flexion are independent. Measuring the distal
            # direction as well would count the same flexion against the limb twice.
            swing = swings[:, 0]
            excursion = float(swing.max())
            activity[limb] = max(bend, excursion)
            if bend >= 0.22:
                mixed[limb] = (bend, excursion)
            signals[limb] = angles if bend >= 0.22 else swing
        minimum = max(0.22, 0.85 * max(activity.values()))
        active = [limb for limb, amplitude in activity.items() if amplitude >= minimum]
        if not active:
            reject("reference_no_motion", "Motion Clip has no measurable dominant limb motion")
        bends = request.mapping.bends
        drivers = [bend.joint for bend in bends.values()]
        if len(set(drivers)) != len(drivers):
            reject("reference_mapping_invalid", "Each driven limb requires a distinct joint")
        for limb in active:
            bend = bends.get(limb)
            tip = TIPS[limb]
            if (
                not bend
                or not bend.gain
                or tip not in targets
                or bend.joint not in qpos
                or joints[bend.joint].kind != "hinge"
                or not coverage(bend.joint, tip)
            ):
                reject(
                    "reference_unmapped_active_limb",
                    "Dominant source limb lacks a mapped actuated hinge",
                    limb=limb,
                    excursionRad=activity[limb],
                )
        for limb, bend in bends.items():
            if limb in mixed and mixed[limb][1] >= minimum:
                flexion, swing = mixed[limb]
                reject(
                    "reference_unmapped_limb_swing",
                    "A bend driver cannot represent a separate dominant proximal swing",
                    limb=limb, bendRad=flexion, swingRad=swing,
                )
            if bend.joint not in qpos or joints[bend.joint].kind != "hinge":
                reject("reference_mapping_invalid", "A bend driver must be an actuated hinge", limb=limb)
        if any(key not in qpos for key in request.mapping.auxiliaryJoints):
            reject("reference_mapping_invalid", "Auxiliary joints must be actuated Asset joints")
        variables = list(dict.fromkeys(drivers + request.mapping.auxiliaryJoints))
        addresses = [qpos[key] for key in variables]
        low = [envelopes[key][0] for key in variables]
        high = [envelopes[key][1] for key in variables]
        rates = [
            request.maxJointSpeed if joints[key].kind == "hinge" else request.maxSlideSpeed
            for key in variables
        ]
        if not derived.fixed_base:
            addresses.append(2)
            low.append(root_position[2] - 0.1)
            high.append(root_position[2] + 0.1)
            rates.append(0.3)
        forward(nominal)
        rest_soles = {name: sole(name).copy() for name in feet}
        frames = []
        previous = nominal[addresses]
        for index, time in enumerate(times):
            goal = {
                bend.joint: nominal[qpos[bend.joint]] + bend.gain * (signals[limb][index] - signals[limb][0])
                for limb, bend in bends.items()
            }
            if index:
                travel = np.asarray(rates) * (time - times[index - 1])
                lower = np.maximum(low, previous - travel)
                upper = np.minimum(high, previous + travel)
            else:
                lower, upper = low, high

            goals = {
                name: rest_points[name] + scale * (source_points[index][name] - source_points[0][name])
                for name in soft_points
            }

            def residual(candidate):
                values = nominal.copy()
                values[addresses] = candidate
                forward(values)
                foot_errors = [
                    (sole(name) - rest_soles[name]).ravel() / math.sqrt(rest_soles[name].size)
                    for name in feet
                ]
                return np.concatenate(
                    (
                        *foot_errors,
                        *[(position(name) - target) * 0.20 for name, target in goals.items()],
                        [(values[qpos[key]] - target) * 0.05 for key, target in goal.items()],
                        (candidate - nominal[addresses]) * 0.001,
                    )
                )

            fit = least_squares(residual, np.clip(previous, lower, upper), bounds=(lower, upper), max_nfev=24)
            values = nominal.copy()
            values[addresses] = previous = fit.x
            frames.append(packet(time, values))
            errors.extend(abs(values[qpos[key]] - value) for key, value in goal.items())
            for name, point in (rest_points | goals).items():
                if name != "pelvis" and (name != "trunk" or name in goals):
                    point_sources[name] = (
                        "source_landmark"
                        if name in goals
                        else ("planted_support" if name in feet else "asset_nominal")
                    )
                    point_errors.setdefault(name, []).append(float(np.linalg.norm(position(name) - point)))
        if max(errors, default=0.0) > 0.18:
            reject(
                "reference_tracking_unreachable",
                "Certified joint or foot bounds prevent requested motion",
                maxBendErrorRad=float(max(errors)),
            )
        for limb in active:
            index = keys.index(bends[limb].joint)
            if np.ptp([frame["joints"][index] for frame in frames]) < 0.02:
                reject(
                    "reference_tracking_unreachable",
                    "Dominant limb motion collapsed during fitting",
                    limb=limb,
                )
        loop = False
        provenance = {
            "clip": {"id": clip["id"], "sha256": clip["sha256"]},
            "video": clip["source"],
            "model": clip["model"],
            "limbExcursionRad": activity,
        }
        license = clip["source"]["license"]["license"]
    else:
        motion = request.keyframes
        if set(motion.jointKeys) != set(keys):
            reject("reference_keyframe_channels", "External keyframes must cover the actual actuated joints")
        frames = []
        for row in motion.frames:
            raw = row.model_dump()
            ordered = dict(zip(motion.jointKeys, row.joints, strict=True))
            raw["joints"] = [ordered[key] for key in keys]
            if request.inPlace:
                raw["position"][:2] = motion.frames[0].position[:2]
            frames.append(packet(row.time, to_qpos(raw)))
        loop = motion.loop
        original = motion.model_dump_json().encode()
        provenance = {
            "keyframes": {
                "sha256": hashlib.sha256(original).hexdigest(),
                "license": request.keyframeLicense.model_dump(exclude_none=True),
            }
        }
        for raw, fitted in zip(motion.frames, frames, strict=True):
            if not raw.bodyLocal:
                continue
            unknown = raw.bodyLocal.keys() - fitted["bodyLocal"].keys()
            if unknown:
                reject(
                    "reference_keyframe_keypoint_invalid",
                    "Unmapped external keypoint target",
                    keys=sorted(unknown),
                )
            for name in targets:
                path = mapping[name]
                if path in raw.bodyLocal:
                    point_sources[name] = "authored_keyframe"
                    point_errors.setdefault(name, []).append(
                        float(np.linalg.norm(np.subtract(raw.bodyLocal[path], fitted["bodyLocal"][path])))
                    )
        license = request.keyframeLicense.license

    if frames[-1]["time"] > KEYFRAME_DURATION:
        reject("reference_motion_limits", "Reference Motion exceeds the duration or frame budget")
    if loop:
        first, last = frames[0], frames[-1]
        if (
            np.max(np.abs(np.subtract(first["joints"], last["joints"]))) > 1e-5
            or np.linalg.norm(np.subtract(first["position"], last["position"])) > 1e-5
            or abs(np.dot(first["orientation"], last["orientation"])) < 1 - 1e-6
        ):
            reject("reference_loop_discontinuity", "Loop pose must close at the final frame")
    else:
        frames.append(frames[-1] | {"time": frames[-1]["time"] + request.terminalHoldSeconds})

    motion = {
        "asset": asset.id,
        "assetContentHash": asset.content_hash,
        "jointKeys": keys,
        "frames": frames,
        "loop": loop,
        "license": license,
    }
    speeds = np.abs(np.diff(np.array([frame["joints"] for frame in frames]), axis=0))
    speeds /= np.diff([frame["time"] for frame in frames])[:, None]
    velocity = [
        request.maxJointSpeed if joints[key].kind == "hinge" else request.maxSlideSpeed for key in keys
    ]
    if np.any(speeds > np.array(velocity)[None, :] + 1e-6):
        reject("reference_joint_speed", "Reference Motion exceeds its certified joint velocity envelope")
    times = np.diff([frame["time"] for frame in frames])
    linear_speed = np.linalg.norm(np.diff([frame["position"] for frame in frames], axis=0), axis=1) / times
    rotations = Rotation.from_quat([frame["orientation"] for frame in frames])
    angular_speed = (rotations[1:] * rotations[:-1].inv()).magnitude() / times
    if np.max(linear_speed) > 0.3 + 1e-6 or np.max(angular_speed) > 4.0 + 1e-6:
        reject("reference_root_speed", "Reference Motion exceeds 0.3 m/s or 4 rad/s root speed")
    samples = sorted(
        set(np.linspace(0, frames[-1]["time"], math.ceil(frames[-1]["time"] * 50) + 1))
        | {frame["time"] for frame in frames}
    )
    body_paths = sorted(derived.body_names_by_path)
    body_indices = [model.body(derived.body_names_by_path[path]).id for path in body_paths]
    parent_indices = {body: index for index, body in enumerate(body_indices)}
    links = [
        [parent_indices[parent], index]
        for index, body in enumerate(body_indices)
        if (parent := int(model.body_parentid[body])) in parent_indices
    ]
    ghost = []
    gaps, margins, slips = [], [], []
    saturated = coordinates = 0
    contact_origins = {}
    for index, time in enumerate(samples):
        values = motion_observations(motion, float(time))["reference_motion_frame"]
        forward(to_qpos({"position": values[:3], "orientation": values[3:7], "joints": values[7:]}))
        if index % 2 == 0 or index == len(samples) - 1:
            ghost.append({"time": float(time), "positions": np.round(data.xpos[body_indices], 7).tolist()})
        for key in keys:
            joint = joints[key]
            value = data.qpos[qpos[key]]
            if not joint.lower - 1e-7 <= value <= joint.upper + 1e-7:
                reject("reference_joint_limit", "Reference Motion exceeds a certified joint limit", joint=key)
            lower, upper = envelopes[key]
            if not lower - 1e-7 <= value <= upper + 1e-7:
                reject(
                    "reference_target_range", "Reference Motion exceeds the actuator target range", joint=key
                )
            saturated += min(value - joint.lower, joint.upper - value) <= 1e-3
            coordinates += 1
        support = []
        for name in feet:
            vertices = sole(name)
            gaps.append(float(abs(vertices[:, 2].min())))
            grounded = np.abs(vertices[:, 2]) <= FLOOR_TOLERANCE
            support.extend(vertices[grounded, :2])
            # Each row is one persistent material vertex in its authored collider.
            origins = contact_origins.setdefault(name, np.full_like(vertices[:, :2], np.nan))
            first_contact = grounded & np.isnan(origins[:, 0])
            origins[first_contact] = vertices[first_contact, :2]
            slips.append(
                float(np.linalg.norm(vertices[grounded, :2] - origins[grounded], axis=1).max(initial=0))
            )
        if len(support) < 3:
            reject("reference_ground_contact", "Mapped soles do not form a ground support polygon")
        try:
            hull = ConvexHull(support)
        except QhullError as exc:
            raise PlanError(
                "reference_ground_contact", "Ground support polygon is degenerate", status=422
            ) from exc
        margins.append(
            float(np.min(-(hull.equations[:, :2] @ data.subtree_com[root, :2] + hull.equations[:, 2])))
        )
        for contact in data.contact:
            if contact.dist < -FLOOR_TOLERANCE and any(model.geom_bodyid[g] == 0 for g in contact.geom):
                reject("reference_ground_penetration", "Collision geometry penetrates the ground")
    if max(gaps) > FLOOR_TOLERANCE or max(slips) > SLIP_TOLERANCE or min(margins) < 0:
        reject(
            "reference_infeasible",
            "Reference Motion fails ground contact, slip or static support checks",
            maxGroundGapM=max(gaps),
            maxFootSlipM=max(slips),
            minSupportMarginM=min(margins),
        )
    preview = []
    for frame in frames:
        forward(to_qpos(frame))
        preview.append({"time": frame["time"], "points": {name: position(name).tolist() for name in targets}})
    report = {
        "oracle": f"MuJoCo {mujoco.__version__} FK and collision geometry",
        "sampleCount": len(samples),
        "sampleRateHz": 50,
        "bounds": {
            "groundGapM": FLOOR_TOLERANCE,
            "footSlipM": SLIP_TOLERANCE,
            "rootSpeedMps": 0.3,
            "rootAngularSpeedRadps": 4.0,
            "jointSpeedRadps": request.maxJointSpeed,
            "slideSpeedMps": request.maxSlideSpeed,
        },
        "maxBendErrorRad": float(max(errors)) if errors else None,
        "keypointErrorM": {name: max(values) for name, values in point_errors.items()},
        "keypointTargets": {
            name: {"path": mapping[name], "source": source} for name, source in point_sources.items()
        },
        "maxRootSpeedMps": float(max(linear_speed)),
        "maxRootAngularSpeedRadps": float(max(angular_speed)),
        "maxGroundGapM": max(gaps),
        "maxFootSlipM": max(slips),
        "minSupportMarginM": min(margins),
        "jointLimitSaturation": float(saturated) / coordinates,
        "maxJointSpeed": {key: float(value) for key, value in zip(keys, speeds.max(axis=0), strict=True)},
    }
    from packages.training.declarative import ReferenceMotion

    motion = ReferenceMotion.model_validate(motion).model_dump(mode="json")
    return {
        "schema": "lw.reference-motion/1",
        "motion": motion,
        "source": provenance,
        "mapping": mapping,
        "inPlace": request.inPlace,
        "report": report,
        "preview": preview,
        "ghost": {"links": links, "samples": ghost},
    }
