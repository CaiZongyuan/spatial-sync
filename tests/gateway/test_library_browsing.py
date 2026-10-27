"""Public browsing remains responsive while filesystem work is pending."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

from fastapi.testclient import TestClient

from apps.api.app.main import create_app


def test_slow_legacy_directory_does_not_block_health(monkeypatch):
    app = create_app()
    with TestClient(app) as client, ThreadPoolExecutor(2) as pool:
        entered, release = Event(), Event()
        original = app.state.manager.library.list

        def slow(*args):
            entered.set()
            assert release.wait(10)
            return original(*args)

        monkeypatch.setattr(app.state.manager.library, "list", slow)
        listing = pool.submit(client.get, "/api/v1/library/scenes")
        try:
            assert entered.wait(5)
            health = pool.submit(client.get, "/api/v1/health")
            assert health.result(timeout=2).status_code == 200
            assert not listing.done()
        finally:
            release.set()
        assert listing.result(timeout=10).status_code == 200


def test_summary_pages_cover_index_without_opening_models_or_previews(monkeypatch):
    app = create_app()
    with TestClient(app) as client:
        library = app.state.manager.library
        prototype = library.versions["lab-demo@1"]
        # A large already-built index; this test measures browse work, not importer throughput.
        library.versions = {
            f"indexed-{index}@1": replace(prototype, name=f"indexed-{index}",
                                         metadata=prototype.metadata | {"title": f"Indexed {index}"})
            for index in range(1000)
        }

        def unexpected(*args, **kwargs):
            raise AssertionError("Browsing must not resolve models or capture snapshots")

        monkeypatch.setattr(library, "resolve", unexpected)
        monkeypatch.setattr(type(prototype), "with_templates", unexpected)
        seen, cursor = [], None
        while True:
            response = client.get("/api/v1/library/scenes", params={
                "view": "summary", "limit": 100, **({"cursor": cursor} if cursor else {}),
            })
            assert response.status_code == 200, response.text
            page = response.json()
            assert page["pagination"]["total"] == 1000
            assert len(page["scenes"]) == 100
            seen.extend(value["id"] for value in page["scenes"])
            cursor = page["pagination"]["nextCursor"]
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) == 1000
        assert client.get("/api/v1/library/scenes?view=summary&q=absent").json()["scenes"] == []
        first = client.get("/api/v1/library/scenes?view=summary&limit=20").json()
        library.versions.pop(seen[-1])
        stale = client.get("/api/v1/library/scenes", params={
            "view": "summary", "limit": 20, "cursor": first["pagination"]["nextCursor"],
        })
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "library_changed"
        assert client.get("/api/v1/library/scenes?view=summary&limit=101").status_code == 422
        library.versions = {"verbose@1": replace(prototype, name="verbose", metadata=prototype.metadata | {
            "title": "Large title " * 1000, "description": "long description " * 10000,
        })}
        bounded = client.get("/api/v1/library/scenes?view=summary&limit=20")
        assert len(bounded.content) < 8192
        assert bounded.json()["scenes"][0]["id"] == "verbose@1"
        assert bounded.json()["scenes"][0]["summaryTruncated"] is True


def test_mutation_during_dependency_inspection_is_not_cached(monkeypatch):
    from template_support import reference_files, scene_files, upload

    from packages.library.scenes import SceneVersion

    app = create_app()
    with TestClient(app) as client:
        asset = upload(client, reference_files()).json()
        assert upload(client, scene_files()[0]).status_code == 200
        library = app.state.manager.library
        library._resolved.clear()
        metadata = app.state.library_assets.resolve(asset["assetVersion"]).path / "asset.yaml"
        original = SceneVersion.with_templates
        changed = False

        def racing(version, roots):
            nonlocal changed
            value = original(version, roots)
            if version.id == "template-slice@1" and not changed:
                changed = True
                metadata.write_bytes(metadata.read_bytes() + b"\n# concurrent forbidden in-place edit\n")
            return value

        monkeypatch.setattr(SceneVersion, "with_templates", racing)
        assert client.get("/api/v1/library/scenes/template-slice/1").status_code == 200
        detail = client.get("/api/v1/library/scenes/template-slice/1").json()
        assert detail["launchable"] is False and detail["templateResolution"]["status"] == "failed"
        assert client.post("/api/v1/sessions", json={"sceneVersion": "template-slice@1"}).status_code == 409
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
