"""Pure SI-unit decisions for measured Newton observations; no engine imports."""

import math

SETTLING_LIMIT_M = 0.005
PENETRATION_LIMIT_M = 0.002
TCP_LIMIT_M = 0.010
DENSITY_MIN_KG_M3 = 50
DENSITY_MAX_KG_M3 = 20_000


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def vector(value):
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(map(finite, value))


def check(identifier, status, message, *, entity=None, location=None, measurements=None, **extra):
    result = {"id": identifier, "tier": "physics", "status": status, "message": message, **extra}
    if entity is not None:
        result["entityKey"] = entity
    if location is not None:
        result["location"] = location
    if measurements is not None:
        result["measurements"] = measurements
    return result


def finite_state(samples):
    """The worker supplies all relevant state arrays after actually loading its model."""
    invalid = [name for name, values in samples.items() if not all(map(finite, values))]
    if not samples or invalid:
        return check("P1", "fail", "Model state has missing or non-finite observations",
                     measurements={"invalidArrays": invalid})
    return check("P1", "pass", "Newton model loaded with finite state",
                 measurements={"observedArrays": list(samples)})


def settling(entity, initial, final):
    if not vector(initial) or not vector(final):
        return check("P2", "fail", "Free-body displacement requires finite positions", entity=entity)
    distance = math.dist(initial, final)
    if not math.isfinite(distance):
        return check("P2", "fail", "Free-body displacement is not finite", entity=entity)
    return check("P2", "pass" if distance <= SETTLING_LIMIT_M else "fail",
                 f"Free-body displacement after 2 s: {distance * 1000:.3f} mm (limit 5 mm)",
                 entity=entity, measurements={"initialM": list(initial), "finalM": list(final),
                                              "displacementM": distance, "limitM": SETTLING_LIMIT_M})


def penetration(contacts, *, world_static_shapes=0):
    """Empty contacts mean a completed collision query, never a missing query."""
    if type(world_static_shapes) is not int or world_static_shapes < 0:
        raise ValueError("World-static shape count must be a nonnegative integer")
    if any(not finite(row[2]) for row in contacts):
        return check("P3", "fail", "Contact separation is not finite")
    worst = min(contacts, key=lambda row: row[2], default=(None, None, 0.0))
    depth = max(0.0, -worst[2])
    excluded = world_static_shapes * (world_static_shapes - 1) // 2
    status = "fail" if depth > PENETRATION_LIMIT_M else "warn" if excluded else "pass"
    return check("P3", status,
                 f"Maximum measured penetration: {depth * 1000:.3f} mm (limit 2 mm)"
                 + (f"; Newton did not query {excluded} world-static shape pairs" if excluded else ""),
                 entity=worst[0],
                 measurements={"otherEntityKey": worst[1], "contactCount": len(contacts),
                               "depthM": depth, "limitM": PENETRATION_LIMIT_M,
                               "worldStaticShapeCount": world_static_shapes,
                               "excludedWorldStaticPairCount": excluded})


def mass_density(entity, mass_kg, volume_m3, *, estimated=False):
    if not finite(mass_kg) or mass_kg <= 0 or not finite(volume_m3) or volume_m3 <= 0:
        return check("P4", "fail", "Physical mass and collision volume must be finite and positive",
                     entity=entity)
    density = mass_kg / volume_m3
    valid = math.isfinite(density) and DENSITY_MIN_KG_M3 <= density <= DENSITY_MAX_KG_M3
    status = "fail" if not valid else "warn" if estimated else "pass"
    return check("P4", status,
                 f"Collision-volume density: {density:.3f} kg/m³ (range 50–20000 kg/m³)"
                 + ("; mass is estimated" if estimated else ""), entity=entity,
                 measurements={"massKg": mass_kg, "collisionVolumeM3": volume_m3,
                               "densityKgM3": density if math.isfinite(density) else None})


def joint_limit(entity, value, lower, upper, *, unit):
    if unit not in ("rad", "m"):
        raise ValueError("A bounded joint coordinate must use rad or m")
    if not finite(value) or any(bound is not None and not finite(bound) for bound in (lower, upper)):
        return check("P5", "fail", "Initial joint coordinate or limit is not finite", entity=entity)
    if lower is None and upper is None:
        return check("P5", "warn", "Joint has no finite position bounds", entity=entity,
                     notApplicable=True)
    valid = (lower is None or value >= lower) and (upper is None or value <= upper)
    return check("P5", "pass" if valid else "fail", f"Initial joint position {value:g} {unit}",
                 entity=entity, measurements={"position": value, "lower": lower, "upper": upper,
                                              "unit": unit})


def tcp_error(entity, location, tcp_world_m, site_world_m):
    if not vector(tcp_world_m) or not vector(site_world_m):
        return check("P6", "fail", "TCP and target Site require finite world positions", entity=entity,
                     location=location)
    error = math.dist(tcp_world_m, site_world_m)
    if not math.isfinite(error * 1000):
        return check("P6", "fail", "TCP error is not finite", entity=entity, location=location)
    return check("P6", "pass" if error <= TCP_LIMIT_M else "fail",
                 f"Taught grasp TCP error: {error * 1000:.3f} mm (limit 10 mm)", entity=entity,
                 location=location, measurements={"tcpWorldM": list(tcp_world_m),
                                                  "siteWorldM": list(site_world_m),
                                                  "errorM": error, "errorMm": error * 1000,
                                                  "limitM": TCP_LIMIT_M})
