"""Read explicitly authored USD solver settings; absent values retain defaults."""

import math

_INTEGER = {"iterations", "ls_iterations", "noslip_iterations"}
_FLOAT = {"impratio", "tolerance", "ls_tolerance"}
_ENUMS = {"integrator": {"Euler", "RK4", "implicit", "implicitfast"}, "cone": {"pyramidal", "elliptic"}}


def authored_settings(stage):
    from pxr import UsdPhysics

    values = {}
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Scene):
            continue
        for name in {"timestep", *_INTEGER, *_FLOAT, *_ENUMS}:
            attribute = prim.GetAttribute("mjc:option:" + name)
            if not attribute or not attribute.HasAuthoredValueOpinion():
                continue
            value = attribute.Get()
            if name in _ENUMS:
                if value not in _ENUMS[name]:
                    raise ValueError("Unsupported authored MuJoCo " + name)
            elif name in _INTEGER:
                if type(value) is not int or value < (0 if name == "noslip_iterations" else 1):
                    raise ValueError("Authored MuJoCo " + name + " must be a valid integer")
            elif type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Authored MuJoCo " + name + " must be positive and finite")
            if name in values:
                same = (
                    values[name] == value
                    if name in _ENUMS or name in _INTEGER
                    else math.isclose(values[name], value, rel_tol=1e-7, abs_tol=1e-12)
                )
                if not same:
                    raise ValueError("Conflicting authored MuJoCo settings: " + name)
            values[name] = value
    return values.pop("timestep", None), values
