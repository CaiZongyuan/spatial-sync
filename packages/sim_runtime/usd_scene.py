"""Offline Y-up USD laboratory fixture, normalized into the protocol's Z-up world."""

from pathlib import Path

SCENE_PATH = Path(__file__).resolve().parents[2] / "examples/usd-lab/lab-y-up.usda"


def usd_lab_builder():
    import newton
    import warp as wp

    from .loaders import import_usd

    with wp.ScopedDevice("cpu"):
        builder = newton.ModelBuilder(up_axis=newton.Axis.Z)
        import_usd(builder, SCENE_PATH)
    return builder
