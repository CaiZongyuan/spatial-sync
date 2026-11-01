"""The joint-space input contract shared by arm Configs and structural validation."""

import math


def posture_problems(document, limits):
    problems = []
    if not isinstance(document, dict) or not isinstance(document.get("locations"), dict):
        return [("locations", "Taught locations require a locations mapping")]
    for name, location in document["locations"].items():
        if not isinstance(location, dict):
            problems.append((name, f"{name} must be a taught location mapping"))
            continue
        for pose in ("approach", "grasp", *(("preInsertion",) if "preInsertion" in location else ())):
            values = location.get(pose)
            if not isinstance(values, list):
                problems.append((name, f"{name}.{pose} is required and must be a joint-coordinate array"))
                continue
            if len(values) != len(limits):
                problems.append(
                    (name, f"{name}.{pose} has {len(values)} coordinates; expected {len(limits)}")
                )
                continue
            for index, (value, (lower, upper)) in enumerate(zip(values, limits, strict=True)):
                if type(value) not in (int, float) or not math.isfinite(value):
                    problems.append((name, f"{name}.{pose}[{index}] requires a finite joint coordinate"))
                elif (lower is not None and value < lower) or (upper is not None and value > upper):
                    problems.append((name, f"{name}.{pose}[{index}] is outside its joint limits"))
    return problems


def require_postures(document, limits):
    problems = posture_problems(document, limits)
    if problems:
        raise ValueError("; ".join(message for _, message in problems))
