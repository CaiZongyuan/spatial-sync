"""Public browsing remains responsive while filesystem work is pending."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from apps.api.app.main import create_app


def test_asset_search_uses_indexed_sizes_without_traversing_package_geometry(monkeypatch):
    app = create_app()
    with TestClient(app) as client:
        library = app.state.library_assets
        expected = {version.id: sum((version.path / name).stat().st_size for name in version.files)
                    for version in library.versions.values()}
        original = Path.rglob

        def guarded(path, *args, **kwargs):
            assert not any(path.is_relative_to(root) for root in library.roots.values()), (
                "An indexed search must not traverse asset packages", path,
            )
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "rglob", guarded)
        response = client.get("/api/v1/library/search?scope=local&limit=24&page=1")
        assert response.status_code == 200, response.text
        assert response.json()["results"]
        for item in response.json()["results"]:
            assert item["local"]["bytes"] == expected[item["id"]]


def test_numbered_asset_pages_build_each_candidate_once_and_read_tool_versions_once(monkeypatch):
    import packages.catalog.search as search
    from packages.library import reports

    app = create_app()
    with TestClient(app) as client:
        library = app.state.library_assets
        prototype = library.versions["robot/fr3@1"]
        library.versions = {
            f"robot/browse-{index}@1": replace(
                prototype, id=f"robot/browse-{index}@1", reports=None,
                metadata=prototype.metadata | {"id": f"robot/browse-{index}",
                                                "title": f"Browse {index:03}"},
                validation={"toolVersions": {"browse-tool": "1"}, "checks": [], "launchable": False},
            ) for index in range(48)
        }
        built, lookups = Counter(), Counter()
        original = search._candidate

        def candidate(match, *args):
            built[match.local.version.id] += 1
            return original(match, *args)

        actual_version = "1"

        def installed(name):
            lookups[name] += 1
            return actual_version

        monkeypatch.setattr(search, "_candidate", candidate)
        monkeypatch.setattr(reports, "installed_version", installed)
        first = client.get("/api/v1/library/search?scope=local&limit=24&page=1")
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["counts"]["matching"] == 48 and body["pagination"]["totalPages"] > 1
        assert len(first.content) <= search.MAX_BYTES
        assert set(built) == set(library.versions)
        assert max(built.values()) == 1
        assert lookups == {"browse-tool": 1}
        assert all(item["local"]["asset"]["validationStatus"] == "available" for item in body["results"])

        # Reuse belongs to one read: environment changes must be visible on the next request.
        actual_version = "2"
        next_read = client.get("/api/v1/library/search?scope=local&limit=24&page=1")
        assert next_read.status_code == 200
        assert lookups == {"browse-tool": 2}
        assert all(item["local"]["asset"]["validationStatus"] == "stale"
                   for item in next_read.json()["results"])


def test_scene_summary_page_reads_shared_tool_metadata_once(monkeypatch):
    from packages.library import reports

    app = create_app()
    with TestClient(app) as client:
        library = app.state.manager.library
        prototype = library.versions["lab-demo@1"]
        library.versions = {
            f"browse-{index}@1": replace(
                prototype, name=f"browse-{index}", reports=None,
                validation={"toolVersions": {"browse-tool": "1"}, "checks": [], "launchable": True},
            ) for index in range(20)
        }
        lookups = Counter()

        def installed(name):
            lookups[name] += 1
            return "1"

        monkeypatch.setattr(reports, "installed_version", installed)
        response = client.get("/api/v1/library/scenes?view=summary&limit=20")
        assert response.status_code == 200, response.text
        assert len(response.json()["scenes"]) == 20
        assert all(item["validationStatus"] == "available" for item in response.json()["scenes"])
        assert lookups == {"browse-tool": 1}


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
