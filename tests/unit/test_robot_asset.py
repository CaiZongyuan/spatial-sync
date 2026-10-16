import hashlib
import json
from pathlib import Path


def test_bundled_official_robot_files_match_pinned_upstream_provenance():
    asset_root = Path(__file__).resolve().parents[2] / "assets/robots"
    manifest = json.loads((asset_root / "franka_emika_panda/provenance.json").read_text(encoding="utf-8"))
    assert manifest["revision"] == "f8fb7abcbeba2318814a74f3eeb02780ad7925d6"
    assert len(manifest["files"]) == 22
    for relative, expected in manifest["files"].items():
        content = (asset_root / relative).read_bytes()
        assert len(content) == expected["bytes"], relative
        assert hashlib.sha256(content).hexdigest() == expected["sha256"], relative
