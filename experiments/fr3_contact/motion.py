"""Endpoint-flat time parameterization of the existing scripted reset moves."""


def quintic_fraction(u):
    """Map u in[0,1] to the same endpoints with zero endpoint speed/acceleration."""
    return u * u * u * (10.0 + u * (-15.0 + 6.0 * u))
