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
