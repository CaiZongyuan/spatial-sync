"""Library previews are real frozen data and never allocate a managed session."""

import json
import shutil
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app import main
from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library import previews
from packages.library.scenes import SceneLibrary
from tools.capture_scene_preview import capture

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_bundled_previews(tmp_path, monkeypatch):
    # A stale operator-cache counterexample must not fall back to a shipped capture.
    monkeypatch.setattr(main, "PreviewRepository", partial(
        previews.PreviewRepository, bundled_root=tmp_path / "empty-bundled-cache"
    ))


async def test_real_preview_preserves_existing_session_and_serves_only_verified_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    version = SceneLibrary(user_root=tmp_path / "library").resolve("lab-demo@1", launch=True)
    captured = await capture(version, "synthetic", tmp_path / "previews")
    assert captured["counts"] == {"physicalEntities": 30, "bindings": 22, "groups": 7}
    dependencies = {item["path"] for item in captured["dependencies"]}
    assert {"apps/api/app/sources/synthetic_source.py", "packages/info/catalog.py",
            "packages/programs/robot_arm/virtual.py", "uv.lock"}.issubset(dependencies)
    recordings = tmp_path / "recordings"
    settings = Settings(recordings_root=recordings, scene_previews_root=tmp_path / "previews")
    with TestClient(create_app(settings)) as client:
        active = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@1"}).json()
        route = "/api/v1/sessions/" + active["sessionId"]
        source = client.app.state.manager.get(active["sessionId"]).source
        detail = client.get("/api/v1/library/scenes/lab-demo/1").json()
        assert detail["preview"]["status"] == "available", detail
        assert detail["entityCount"] == 30 and detail["deviceCount"] == 22
        assert detail["templateResolution"] == {"status": "not_required", "error": None}
        assert detail["effectiveBindings"] == {}
        assert detail["entities"] == detail["rawBindings"]["entities"] == version.bindings["entities"]
        assert any(binding.get("match") == "/World/Lab/Tubes/*" for binding in detail["entities"])
        manifest = client.get(detail["preview"]["documents"]["manifest"]).json()
        assert manifest["sessionId"] != active["sessionId"]
        assert next(site for site in manifest["sites"] if site["entityKey"] == "/World/Lab/Rack/A1")[
            "localPose"
        ]["p"] == version.locations["locations"]["rack.A1"]["p"]
        catalog = client.get(detail["preview"]["documents"]["catalog"]).json()
        assert len(catalog["entities"]) == 22
        assert "/World/Lab/Tubes/Tube20" in catalog["entities"]
        binary = client.get(detail["preview"]["documents"]["spatial"]).content
        assert binary[:4] == b"NSYN"
        base = "/api/v1/library/scenes/lab-demo/1/preview/synthetic/"
        for relative in ("capture.json", "../../uv.lock", "C:/private.json", "not-indexed.json"):
            assert client.get(base + relative).status_code == 404
        health = client.get("/api/v1/health").json()
        assert health["sessionCount"] == 1
        preserved = client.get(route).json()
        assert preserved["sceneRevision"] == active["sceneRevision"] and preserved["state"] == "RUNNING"
        assert client.app.state.manager.get(active["sessionId"]).source is source
        assert [path.name for path in recordings.iterdir()] == [active["sessionId"]]
        action = client.post(route + "/actions", json={
            "requestId": "preview-did-not-disturb-device", "entityKey": "/World/Lab/Centrifuge",
            "action": "closeLid", "wait": True,
        })
        assert action.status_code == 200 and action.json()["status"] == "succeeded"


async def test_missing_wrong_source_or_stale_preview_does_not_change_launchability(tmp_path, monkeypatch):
    user_root = tmp_path / "library"
    package = user_root / "scenes/lab-demo/4"
    shutil.copytree(ROOT / "library/scenes/lab-demo/1", package)
    report_file = package / "validation.json"
    report = json.loads(report_file.read_bytes())
    report["subject"] = "scene:lab-demo@4"
    report_file.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setenv("LW_LIBRARY", str(user_root))
    library = SceneLibrary(user_root=user_root)
    version = library.resolve("lab-demo@1", launch=True)
    await capture(version, "synthetic", tmp_path / "previews")
    settings = Settings(recordings_root=tmp_path / "recordings", scene_previews_root=tmp_path / "previews")
    with TestClient(create_app(settings)) as client:
        missing = client.get("/api/v1/library/scenes/lab-demo/4").json()
        assert missing["launchable"] is True and missing["preview"]["status"] == "unavailable"
        assert missing["entityCount"] is None and missing["deviceCount"] is None
        wrong_source = client.get("/api/v1/library/scenes/lab-demo/1?source=newton").json()
        assert wrong_source["preview"]["status"] == "unavailable"
        assert "物理源" in wrong_source["preview"]["reason"]
        index = next((tmp_path / "previews").rglob("capture.json"))
        value = json.loads(index.read_bytes())
        value["dependencies"][0]["sha256"] = "0" * 64
        index.write_text(json.dumps(value), encoding="utf-8")
        stale = client.get("/api/v1/library/scenes/lab-demo/1").json()
        assert stale["launchable"] is True and stale["preview"]["status"] == "unavailable"
        assert "输入已变化" in stale["preview"]["reason"]
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
        assert not (tmp_path / "recordings").exists()


async def test_capture_refuses_to_write_inside_published_packages(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    version = SceneLibrary(user_root=tmp_path / "library").resolve("lab-demo@1", launch=True)
    before = {path.name: path.read_bytes() for path in version.path.iterdir() if path.is_file()}
    with pytest.raises(ValueError, match="outside published"):
        await capture(version, "synthetic", version.path / "previews")
    assert {path.name: path.read_bytes() for path in version.path.iterdir() if path.is_file()} == before


async def test_preview_input_digest_is_portable_but_rejects_real_changes(tmp_path, monkeypatch):
    """A checkout line ending is not a source edit; output bytes remain exact."""
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    version = SceneLibrary(user_root=tmp_path / "library").resolve("lab-demo@1", launch=True)
    captured = await capture(version, "synthetic", tmp_path / "previews")
    assert captured["dependencyDigest"] == previews.DEPENDENCY_DIGEST
    original_path = previews.dependency_path
    source_name = "apps/api/app/sources/synthetic_source.py"
    source = original_path({"path": source_name}).read_bytes().replace(b"\r\n", b"\n")
    materialized = tmp_path / "synthetic_source.py"

    def dependency_path(item):
        path = original_path(item)
        return materialized if item["path"] == source_name else path

    monkeypatch.setattr(previews, "dependency_path", dependency_path)
    settings = Settings(recordings_root=tmp_path / "recordings", scene_previews_root=tmp_path / "previews")
    with TestClient(create_app(settings)) as client:
        route = "/api/v1/library/scenes/lab-demo/1"
        for content in (source, source.replace(b"\n", b"\r\n")):
            materialized.write_bytes(content)
            assert client.get(route).json()["preview"]["status"] == "available"
        materialized.write_bytes(source.replace(b"class SyntheticSource", b"class ChangedSource", 1))
        assert client.get(route).json()["preview"]["status"] == "unavailable"
        materialized.write_bytes(source)
        index = next((tmp_path / "previews").rglob("capture.json"))
        snapshot = index.parent / captured["documents"]["spatial"]
        payload = snapshot.read_bytes()
        snapshot.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
        changed = client.get(route).json()["preview"]
        assert changed["status"] == "unavailable" and "文件校验失败" in changed["reason"]
        snapshot.write_bytes(payload)
        unsupported = {**captured, "dependencyDigest": "unknown"}
        index.write_text(json.dumps(unsupported), encoding="utf-8")
        assert "摘要规则不受支持" in client.get(route).json()["preview"]["reason"]
        manifest_path = index.parent / captured["documents"]["manifest"]
        manifest = json.loads(manifest_path.read_bytes())
        visual = next(visual for node in manifest["nodes"] for visual in node.get("visuals", [])
                      if visual["kind"] != "primitive")
        visual["url"] = "https://untrusted.invalid/model.glb"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        captured["files"][captured["documents"]["manifest"]] = previews.file_hash(manifest_path)
        index.write_text(json.dumps(captured), encoding="utf-8")
        assert "未索引的资产" in client.get(route).json()["preview"]["reason"]
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
        assert not (tmp_path / "recordings").exists()


def test_only_source_input_text_normalizes_line_endings(tmp_path):
    source, binary = tmp_path / "source.py", tmp_path / "mesh.bin"
    for path in (source, binary):
        path.write_bytes(b"first\r\nsecond\r\n")
    source_input, source_file = previews.input_hash(source), previews.file_hash(source)
    binary_input = previews.input_hash(binary)
    for path in (source, binary):
        path.write_bytes(b"first\nsecond\n")
    assert previews.input_hash(source) == source_input
    assert previews.file_hash(source) != source_file
    assert previews.input_hash(binary) != binary_input
    assert previews.input_hash(binary) == previews.file_hash(binary)


async def test_malformed_capture_metadata_keeps_public_library_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    version = SceneLibrary(user_root=tmp_path / "library").resolve("lab-demo@1", launch=True)
    captured = await capture(version, "synthetic", tmp_path / "previews")
    index = next((tmp_path / "previews").rglob("capture.json"))
    settings = Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings",
                        scene_previews_root=tmp_path / "previews")
    mutations = [
        ("capturedAt", None, True), ("simTime", None, True), ("runtime", None, True),
        ("capturedAt", "not-a-date", False), ("capturedAt", "2026-10-19T00:00:00", False),
        ("simTime", -1, False), ("simTime", float("nan"), False), ("simTime", True, False),
        ("simTime", 1, False),
        ("runtime", None, False), ("runtime", {}, False), ("runtime", {"source": []}, False),
        ("documents", None, False), ("thumbnail", None, False),
        ("bounds", {"min": [0], "max": [1], "scope": "model"}, False),
        ("bounds", {"min": [1, 0, 0], "max": [0, 1, 1], "scope": "model"}, False),
    ]
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/api/v1/library/scenes/lab-demo/1").json()["preview"]["status"] == "available"
        for field, replacement, missing in mutations:
            value = {**captured}
            if missing:
                value.pop(field)
            else:
                value[field] = replacement
            index.write_text(json.dumps(value), encoding="utf-8")
            detail = client.get("/api/v1/library/scenes/lab-demo/1")
            assert detail.status_code == 200, (field, replacement, detail.text)
            unavailable = detail.json()
            assert unavailable["preview"]["status"] == "unavailable", (field, replacement)
            assert unavailable["preview"]["reason"] and unavailable["launchable"] is True
            assert unavailable["entityCount"] is None and unavailable["deviceCount"] is None
            listing = client.get("/api/v1/library/scenes?q=lab-demo")
            assert listing.status_code == 200
            assert listing.json()["scenes"][0]["preview"]["status"] == "unavailable"
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
        assert not (tmp_path / "recordings").exists()


async def test_malformed_operator_metadata_can_use_a_valid_bundled_capture(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    version = SceneLibrary(user_root=tmp_path / "library").resolve("lab-demo@1", launch=True)
    captured = await capture(version, "synthetic", tmp_path / "previews")
    shutil.copytree(tmp_path / "previews", tmp_path / "bundled")
    monkeypatch.setattr(main, "PreviewRepository", partial(
        previews.PreviewRepository, bundled_root=tmp_path / "bundled"
    ))
    index = next((tmp_path / "previews").rglob("capture.json"))
    del captured["runtime"]
    index.write_text(json.dumps(captured), encoding="utf-8")
    settings = Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings",
                        scene_previews_root=tmp_path / "previews")
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        detail = client.get("/api/v1/library/scenes/lab-demo/1")
        assert detail.status_code == 200
        assert detail.json()["preview"]["status"] == "available"
        snapshot = client.get(detail.json()["preview"]["documents"]["spatial"])
        assert snapshot.status_code == 200 and snapshot.content.startswith(b"NSYN")
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
