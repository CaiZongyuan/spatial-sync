"""Read actual contact facts for declared moving bodies; never invent empty data."""

import math
from numbers import Real

CONTACT_LIMIT_M = 0.001


def validate_expected_contacts(pairs):
    """Canonical exact shape identities shared by runtime and offline sweep."""
    if not isinstance(pairs, (list, tuple)):
        raise ValueError("Expected contacts require a list of exact shape pairs")
    result = set()
    for pair in pairs:
        if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                or any(not isinstance(k, str) or not k.startswith("/") or any(c in k for c in "*?[]")
                       for k in pair) or pair[0] == pair[1]):
            raise ValueError("Expected contacts require two distinct absolute shape keys without wildcards")
        key = tuple(sorted(pair))
        if key in result:
            raise ValueError("Expected contact pairs must be unique")
        result.add(key)
    return frozenset(result)


def classify_contact(shapes, declarations, *, bodies=(), finger_bodies=(), grasp_bodies=()):
    """Candidate grasp pairs activate only for the actual candidate/held assembly."""
    if tuple(sorted(shapes)) not in declarations:
        return False
    if finger_bodies:
        if len(bodies) != 2:
            raise ValueError("Contact classification needs both actual shape owners")
        for index, body in enumerate(bodies):
            if body in finger_bodies:
                return bodies[1 - index] in grasp_bodies
    return True


def validate_seating_contacts(pairs, allowance_m):
    """Bounded demo-only tube/rack floor pairs; never body or prefix exemptions."""
    if (isinstance(allowance_m, bool) or not isinstance(allowance_m, Real)
            or not math.isfinite(allowance_m) or not 0 < allowance_m <= .005):
        raise ValueError("Seating contact allowance must be finite and at most 5 mm")
    if not isinstance(pairs, (list, tuple)) or len(pairs) > 8:
        raise ValueError("Seating contacts require at most eight exact shape pairs")
    result = set()
    for pair in pairs:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(not isinstance(shape, str) or not shape.startswith("/") for shape in pair)):
            raise ValueError("Seating contacts require two absolute shape keys")
        valid = False
        for a, b in (pair, pair[::-1]):
            for name in ("S1", "S2", "S3", "S4", "S5", "S6", "B5", "B6"):
                tail = f"/Labware/{name}/Floor"
                if (a.endswith(tail) and b == a[:-len(tail)]
                        + f"/Labware/CollectionRack/Frame/{name}Floor"):
                    valid = True
        key = frozenset(pair)
        if not valid or key in result:
            raise ValueError("Seating contacts require unique corresponding tube/rack floor pairs")
        result.add(key)
    return result


def contacts_for_bodies(port, bodies, *, limit_m=CONTACT_LIMIT_M, seating_pairs=(),
                        seating_limit_m=.002, on_seating_contact=None, expected_pairs=(),
                        on_expected_contact=None, finger_bodies=(), grasp_bodies=()):
    if not bodies:
        raise ValueError("Moving collision scope is unavailable")
    try:
        if not math.isfinite(limit_m) or not 0 < limit_m <= .002:
            raise ValueError("Contact allowance must be finite and at most 2 mm")
        seating = validate_seating_contacts(seating_pairs, seating_limit_m)
        expected = validate_expected_contacts(expected_pairs)
        if expected and seating:
            raise ValueError("Expected classification cannot mix with legacy seating exceptions")
        contacts = port.collision_contacts(max_distance=0. if expected else -limit_m, bodies=bodies)
        result, accepted, expected_rows = [], {}, {}
        for row in contacts:
            if (len(row.bodies) != 2 or None in row.bodies or len(row.shapes) != 2
                    or any(not isinstance(shape, str) or not shape for shape in row.shapes)
                    or isinstance(row.distance_m, bool) or not isinstance(row.distance_m, Real)
                    or not math.isfinite(row.distance_m)):
                raise ValueError("Actual contact facts are incomplete")
            if any(body in bodies for body in row.bodies) and classify_contact(
                row.shapes, expected, bodies=row.bodies, finger_bodies=finger_bodies,
                grasp_bodies=grasp_bodies,
            ):
                key = tuple(sorted(row.shapes))
                if key not in expected_rows or row.distance_m < expected_rows[key].distance_m:
                    expected_rows[key] = row
            elif row.distance_m < -limit_m and any(body in bodies for body in row.bodies):
                key = frozenset(row.shapes)
                if key in seating and row.distance_m >= -max(limit_m, seating_limit_m):
                    if key not in accepted or row.distance_m < accepted[key].distance_m:
                        accepted[key] = row
                else:
                    result.append(row)
        if on_seating_contact:
            for row in accepted.values():
                on_seating_contact(shapes=list(row.shapes), depthM=-row.distance_m, limitM=seating_limit_m)
        if on_expected_contact:
            for row in expected_rows.values():
                on_expected_contact(shapes=list(row.shapes), depthM=max(0., -row.distance_m))
        return result
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError("Actual contact facts are unavailable") from error
