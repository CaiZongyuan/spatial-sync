"""Clock intent and conflicting declarations are checked without loading Newton."""

import pytest
from pxr import Sdf, Usd, UsdPhysics

from packages.library.metadata import Simulation
from packages.sim_runtime.usd_settings import authored_settings


def setting(stage, path, key, value):
    prim = UsdPhysics.Scene.Define(stage, path).GetPrim()
    kind = Sdf.ValueTypeNames.Token if isinstance(value, str) else (
        Sdf.ValueTypeNames.Int if type(value) is int else Sdf.ValueTypeNames.Double)
    prim.CreateAttribute("mjc:option:" + key, kind, custom=True).Set(value)


def test_animation_frame_rate_does_not_select_a_physics_clock():
    stage = Usd.Stage.CreateInMemory()
    stage.SetTimeCodesPerSecond(30)
    UsdPhysics.Scene.Define(stage, "/Physics")
    assert authored_settings(stage) == (None, {})
    assert Simulation().physicsDt is None


def test_explicit_source_options_and_matching_composed_declarations_are_retained():
    stage = Usd.Stage.CreateInMemory()
    for path in ("/Physics", "/ReferencedAsset/Physics"):
        setting(stage, path, "timestep", .0005)
        setting(stage, path, "iterations", 60)
        setting(stage, path, "noslip_iterations", 4)
        setting(stage, path, "cone", "elliptic")
        setting(stage, path, "integrator", "implicitfast")
    dt, options = authored_settings(stage)
    assert dt == .0005
    assert options == {"iterations": 60, "noslip_iterations": 4,
                       "cone": "elliptic", "integrator": "implicitfast"}
    assert Simulation(physicsDt=dt).physicsDt == dt


@pytest.mark.parametrize("key,value", [("timestep", 0.), ("timestep", float("nan")),
                                       ("iterations", -1), ("cone", "unknown")])
def test_invalid_source_settings_are_exposed_before_model_loading(key, value):
    stage = Usd.Stage.CreateInMemory()
    setting(stage, "/Physics", key, value)
    with pytest.raises(ValueError, match="Authored|Unsupported"):
        authored_settings(stage)


def test_composed_sources_cannot_silently_choose_between_two_clocks():
    stage = Usd.Stage.CreateInMemory()
    setting(stage, "/Physics", "timestep", .0005)
    setting(stage, "/Asset/Physics", "timestep", 1 / 240)
    with pytest.raises(ValueError, match="Conflicting.*timestep"):
        authored_settings(stage)


def test_composed_integer_solver_settings_must_match_exactly():
    stage = Usd.Stage.CreateInMemory()
    setting(stage, "/Physics", "iterations", 10_000_000)
    setting(stage, "/Asset/Physics", "iterations", 10_000_001)
    with pytest.raises(ValueError, match="Conflicting.*iterations"):
        authored_settings(stage)
