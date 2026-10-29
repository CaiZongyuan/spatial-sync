"""Procedural assets cross the same S4 boundary as public library imports."""

import yaml

from factory.cell_passage import package, vessel
from packages.library.structural import validate
from packages.library.validation import StructuralValidator


def test_authored_asset_passes_s4_and_changed_source_is_rejected(tmp_path):
    path = package(tmp_path, "passage-provenance-test", vessel)
    payload = {
        "kind": "asset",
        "subject": "asset:labware/passage-provenance-test@1",
        "metadata": yaml.safe_load((path / "asset.yaml").read_text()),
        "package": StructuralValidator.scope(path),
        "assets": [],
        "types": {},
        "programs": {},
    }

    def s4():
        return next(row for row in validate(payload)["checks"] if row["id"] == "S4")

    assert s4()["status"] == "pass"
    with (path / "asset.usda").open("a") as stream:
        stream.write("\n# Changed after the source receipt was frozen.\n")
    changed = s4()
    assert changed["status"] == "fail"
    assert "Source hash differs: asset.usda" in changed["message"]
