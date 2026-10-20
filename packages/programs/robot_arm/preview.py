"""Taught joint paths and bounded FK samples shared by both arm programs."""

from dataclasses import dataclass

import numpy as np

from packages.info.preview import PathPreview

from .trajectory import plan_trajectory


def taught_waypoints(location):
    return location["approach"], location["grasp"], location["approach"]


def joint_targets(action, params, locations, home):
    if action == "home":
        return [home]
    if action == "moveJoints":
        return [params["positions"]]
    if action == "moveTo":
        return [locations[params["location"]][params.get("pose", "approach")]]
    if action in ("pick", "place", "pickPlace"):
        names = [params["from"], params["to"]] if action == "pickPlace" else [params["location"]]
        return [target for name in names for target in taught_waypoints(locations[name])]
    return None


@dataclass(frozen=True)
class JointPathSample:
    segment: int
    elapsed: float
    joints: np.ndarray


def sample_joint_path(start, targets, limits, *, speed_scale=1.0, speed=1.0,
                      minimum_duration=0.0, max_points=64):
    if type(max_points) is not int or not 2 <= max_points <= 64 or targets is None or len(targets) == 0:
        raise ValueError("A joint path needs targets and a sample budget from2 through64")
    if not np.isfinite(minimum_duration) or minimum_duration < 0 or len(targets) + 1 > max_points:
        raise ValueError("The sample budget must preserve every motion boundary")
    motions, previous = [], start
    for target in targets:
        motions.append(plan_trajectory(previous, target, limits, speed_scale=speed_scale, speed=speed))
        previous = target
    samples = [JointPathSample(0, 0.0, motions[0].start.copy())]
    if all(np.array_equal(motion.start, motion.end) for motion in motions):
        return samples
    durations = np.array([max(minimum_duration, motion.duration) for motion in motions])
    weights = durations if np.sum(durations) > 0 else np.ones(len(motions))
    extra = (max_points - 1 - len(motions)) * weights / np.sum(weights)
    intervals = np.floor(extra).astype(int) + 1
    remaining = max_points - 1 - int(np.sum(intervals))
    for index in sorted(range(len(motions)), key=lambda i: (-(extra[i] % 1), i))[:remaining]:
        intervals[index] += 1
    for index, (motion, duration, count) in enumerate(zip(motions, durations, intervals, strict=True)):
        for elapsed in np.linspace(0, duration, int(count) + 1)[1:]:
            samples.append(JointPathSample(index, float(elapsed), motion.sample(float(elapsed))[0]))
    return samples


def path_preview(start, targets, limits, forward, *, speed_scale=1.0, speed=1.0,
                 minimum_duration=0.0, max_points=64):
    samples = sample_joint_path(start, targets, limits, speed_scale=speed_scale, speed=speed,
                                minimum_duration=minimum_duration, max_points=max_points)
    poses = [forward(sample.joints) for sample in samples]
    return PathPreview(points=[tuple(map(float, pose["p"])) for pose in poses],
                       startJointPositions=list(map(float, start)),
                       targetJointPositions=list(map(float, targets[-1])), targetPose=poses[-1])
