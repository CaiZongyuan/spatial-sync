"""Asset solver intent is explicit and old authored documents remain unchanged."""

import pytest
import yaml
from pydantic import ValidationError

from packages.library.metadata import AssetMetadata, Simulation, asset_metadata


def metadata():
    return {"id": "robot/declared-solver", "kind": "robot", "title": "Declared solver",
            "stage": "asset.usda"}


def test_optional_asset_simulation_reuses_scene_schema_without_filling_old_documents(tmp_path):
    original = metadata()
    path = tmp_path / "asset.yaml"
    raw = yaml.safe_dump(original).encode()
    path.write_bytes(raw)
    (tmp_path / "asset.usda").write_text("#usda 1.0\n", encoding="utf-8")
    assert AssetMetadata.model_validate(original).simulation is None
    assert asset_metadata(tmp_path) == original
    assert path.read_bytes() == raw
    declared = original | {"simulation": {"solver": "mujoco", "physicsDt": .0005, "warmupSteps": 2}}
    parsed = AssetMetadata.model_validate(declared)
    assert isinstance(parsed.simulation, Simulation)
    assert parsed.simulation.model_dump(exclude_none=True) == declared["simulation"]


@pytest.mark.parametrize("settings", [{"solver": "auto"}, {"physicsDt": 0.},
                                      {"physicsDt": float("nan")}, {"warmupSteps": 0},
                                      {"solver": "mujoco", "inferred": True}])
def test_asset_simulation_rejects_invalid_or_implicit_policy(settings):
    with pytest.raises(ValidationError):
        AssetMetadata.model_validate(metadata() | {"simulation": settings})
