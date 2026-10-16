"""Trusted scene construction helpers; HTTP callers use registered scene IDs."""

from pathlib import Path


def import_robot(builder, path: Path, *, prefix: str):
    import newton

    child = newton.ModelBuilder(up_axis=newton.Axis.Z)
    if path.suffix.lower() == ".urdf":
        child.add_urdf(str(path))
    elif path.suffix.lower() in (".xml", ".mjcf"):
        child.add_mjcf(str(path))
    else:
        raise ValueError(f"Unsupported robot format: {path.suffix}")
    builder.add_builder(child, label_prefix=prefix)


def import_usd(builder, path: Path):
    """Apply stage axis conversion once and make labels independent of importer naming."""
    paths = builder.add_usd(str(path), apply_up_axis_from_stage=True)
    for kind in ("body", "shape", "joint"):
        labels = getattr(builder, f"{kind}_label")
        for prim_path, index in paths[f"path_{kind}_map"].items():
            labels[index] = str(prim_path)
    return paths
