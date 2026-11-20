"""Body-frame velocity guidance shared by generic and biped Policy Programs."""

import math


def wrap_pi(angle):
    return (angle + math.pi) % math.tau - math.pi


def slot_command_bounds(slot) -> list[tuple[float, float]] | None:
    cmd_terms = [item for item in slot.spec.observations if item.source == "command"]
    if not cmd_terms:
        return None
    width = cmd_terms[0].shape[0]
    lower = [-math.inf] * width
    upper = [math.inf] * width
    for term in cmd_terms:
        for idx, (lo, hi) in enumerate(zip(term.lower, term.upper, strict=True)):
            lower[idx] = max(lower[idx], float(lo))
            upper[idx] = min(upper[idx], float(hi))
    envelope = slot.task.get("episode", {}).get("commandEnvelope")
    if envelope is not None and len(envelope) == width:
        for idx, (lo, hi) in enumerate(envelope):
            lower[idx] = max(lower[idx], float(lo))
            upper[idx] = min(upper[idx], float(hi))
    return list(zip(lower, upper, strict=True))


def waypoint_command(wp, p, yaw_rad, cmd_bounds, config, speed_override, prev):
    width = len(cmd_bounds)
    dx = float(wp["p"][0]) - float(p[0])
    dy = float(wp["p"][1]) - float(p[1])
    dist = math.hypot(dx, dy)
    target_yaw = float(wp["yaw"])
    final_yaw_err = wrap_pi(target_yaw - yaw_rad)

    xy_tol = float(wp.get("xyTolerance") or config.xyTolerance)
    yaw_tol = float(wp.get("yawTolerance") or config.yawTolerance)

    if dist <= xy_tol and abs(final_yaw_err) <= yaw_tol:
        return [max(lo, min(hi, 0.0)) for lo, hi in cmd_bounds], [0.0, 0.0, 0.0]

    lin_cap = float(config.maxLinearSpeed)
    lat_cap = float(config.maxLateralSpeed)
    if speed_override is not None:
        lin_cap = min(lin_cap, float(speed_override))
        lat_cap = min(lat_cap, float(speed_override))
    yaw_cap = float(config.maxYawRate)

    if dist <= xy_tol:
        vx_des, vy_des = 0.0, 0.0
        wz_des = max(-yaw_cap, min(yaw_cap, config.yawGain * final_yaw_err))
    else:
        dx_body = math.cos(yaw_rad) * dx + math.sin(yaw_rad) * dy
        dy_body = -math.sin(yaw_rad) * dx + math.cos(yaw_rad) * dy
        bearing_err = wrap_pi(math.atan2(dy, dx) - yaw_rad)
        if dist > xy_tol * 2.5 and abs(bearing_err) > config.headingAlignRad:
            yaw_err = bearing_err
            align_scale = max(0.2, math.cos(bearing_err)) if cmd_bounds[0][0] >= -1e-6 else 1.0
        else:
            yaw_err = final_yaw_err
            align_scale = 1.0
        vx_des = max(-lin_cap, min(lin_cap, config.linearGain * dx_body * align_scale))
        vy_des = max(-lat_cap, min(lat_cap, config.linearGain * dy_body * align_scale))
        wz_des = max(-yaw_cap, min(yaw_cap, config.yawGain * yaw_err))

    alpha = float(config.commandSmoothing)
    smoothed = [
        prev[0] + alpha * (vx_des - prev[0]),
        prev[1] + alpha * (vy_des - prev[1]),
        prev[2] + alpha * (wz_des - prev[2]),
    ]

    raw_vec = [smoothed[0], smoothed[2]] if width == 2 else smoothed[:width]
    if len(raw_vec) < width:
        raw_vec.extend([0.0] * (width - len(raw_vec)))
    return [float(max(lo, min(hi, val))) for val, (lo, hi) in zip(raw_vec, cmd_bounds, strict=True)], smoothed
