"""Preview identity metadata retains legacy readers and strict exact asset pins."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.library.previews import _CaptureMetadata

ROOT = Path(__file__).resolve().parents[2]


def recorded_metadata():
    # Real bundled metadata is the compatibility input; this test does not claim
    # that its old producer hashes validate a newly changed source tree.
    path = next((ROOT / "assets/scene-previews").rglob("capture.json"))
    return json.loads(path.read_bytes())


def test_legacy_metadata_defaults_to_no_usd_assets_and_exact_new_pins_round_trip():
    legacy = recorded_metadata()
    legacy.pop("libraryAssets", None)
    assert _CaptureMetadata.model_validate_json(json.dumps(legacy)).libraryAssets == []
    modern = deepcopy(legacy) | {"libraryAssets": [
        {"id": "environment/usd-fixture-room@1", "contentHash": "a" * 64},
        {"id": "labware/usd-fixture-tube@1", "contentHash": "b" * 64},
    ]}
    parsed = _CaptureMetadata.model_validate_json(json.dumps(modern)).model_dump(mode="json")
    assert parsed["libraryAssets"] == modern["libraryAssets"]


@pytest.mark.parametrize("pins", [
    None, "invalid", [{"id": "environment/usd-fixture-room@1"}],
    [{"id": "environment/usd-fixture-room", "contentHash": "a" * 64}],
    [{"id": "environment/usd-fixture-room@1", "contentHash": "bad"}],
    [{"id": "environment/usd-fixture-room@1", "contentHash": "a" * 64}] * 2,
])
def test_malformed_or_ambiguous_library_identity_is_rejected(pins):
    with pytest.raises(ValidationError, match="libraryAssets"):
        _CaptureMetadata.model_validate_json(json.dumps(recorded_metadata() | {"libraryAssets": pins}))
