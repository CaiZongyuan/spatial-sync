"""A retained public validation failure must stop before session admission."""

import json

import pytest

from apps.cli.lw.client import GatewayError
from factory import cell_passage_public
from packages.library.common import content_hash
from tools.cell_passage import _install


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


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", ["exact", "different", "missing_hash", "not_found"])
async def test_dependency_reuse_requires_the_exact_public_upload_hash(tmp_path, cached):
    library, output = tmp_path / "library", tmp_path / "receipt"
    asset = library / "assets/labware/cache-test/1"
    scene = library / "scenes/cell-passage/1"
    asset.mkdir(parents=True)
    scene.mkdir(parents=True)
    output.mkdir()
    (asset / "asset.yaml").write_text("id: labware/cache-test\n")
    (scene / "scene.yaml").write_text("id: cell-passage\n")
    imported = []
    asset_hash, scene_hash = content_hash(asset), content_hash(scene)

    class Client:
        async def library_describe(self, identifier, *, view):
            assert identifier == "labware/cache-test@1" and view == "full"
            if cached == "not_found":
                raise GatewayError("Not found", status_code=404)
            validation = {"checks": [], "importContentHash": asset_hash if cached == "exact" else "0" * 64}
            if cached == "missing_hash":
                validation.pop("importContentHash")
            return {"assetVersion": identifier, "contentHash": "1" * 64, "validation": validation}

        async def library_import(self, path):
            imported.append(path)
            kind = "assetVersion" if path == asset else "sceneVersion"
            return {kind: "labware/cache-test@1" if path == asset else "cell-passage@2",
                    "validation": {"checks": [], "importContentHash": content_hash(path)}}

        async def library_validate(self, identifier):
            assert identifier == "cell-passage@2"
            return {"launchable": True, "checks": []}

    assert await _install(Client(), library, output, scene_hash) == "cell-passage@2"
    assert imported == ([scene] if cached == "exact" else [asset, scene])
    receipt = json.loads((output / "imports.json").read_text())[0]
    assert (receipt.get("observation") == "existing_exact_upload_hash") == (cached == "exact")
