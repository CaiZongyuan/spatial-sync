"""Read actual contact facts for declared moving bodies; never invent empty data."""

import math
from numbers import Real

CONTACT_LIMIT_M = 0.001


def contacts_for_bodies(port, bodies):
    if not bodies:
        raise ValueError("Moving collision scope is unavailable")
    try:
        contacts = port.collision_contacts(max_distance=-CONTACT_LIMIT_M)
        result = []
        for row in contacts:
            if (len(row.bodies) != 2 or None in row.bodies or len(row.shapes) != 2
                    or any(not isinstance(shape, str) or not shape for shape in row.shapes)
                    or isinstance(row.distance_m, bool) or not isinstance(row.distance_m, Real)
                    or not math.isfinite(row.distance_m)):
                raise ValueError("Actual contact facts are incomplete")
            if row.distance_m < -CONTACT_LIMIT_M and any(body in bodies for body in row.bodies):
                result.append(row)
        return result
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError("Actual contact facts are unavailable") from error
