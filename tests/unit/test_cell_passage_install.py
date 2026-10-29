"""A retained public validation failure must stop before session admission."""

import json

import pytest

from factory import cell_passage_public
from packages.library.common import content_hash


@pytest.mark.asyncio
async def test_public_launch_keeps_nonlaunchable_report_without_posting_session(tmp_path, monkeypatch):
    library, output = tmp_path / "library", tmp_path / "receipt"
    (library / "assets").mkdir(parents=True)
    scene = library / "scenes/cell-passage/1"
    scene.mkdir(parents=True)
    (scene / "scene.yaml").write_text("id: cell-passage\n")
    report = {
        "launchable": False,
        "checks": [{"id": "P6", "status": "fail", "message": "Taught grasp mismatch"}],
        "importContentHash": content_hash(scene),
    }
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, path, **kwargs):
            calls.append((method, path))
            assert (method, path) == ("GET", "/api/v1/health")
            return {"sessionCount": 0}

        async def library_import(self, path):
            return {"sceneVersion": "cell-passage@1", "validation": report}

        async def library_validate(self, identifier):
            return report

    monkeypatch.setattr(cell_passage_public, "GatewayClient", Client)
    with pytest.raises(ValueError, match="not launchable"):
        await cell_passage_public.launch(library, "http://owned", output)
    assert json.loads((output / "validation.json").read_text()) == report
    assert json.loads((output / "imports.json").read_text())[0]["sceneVersion"] == "cell-passage@1"
    assert calls == [("GET", "/api/v1/health")]
