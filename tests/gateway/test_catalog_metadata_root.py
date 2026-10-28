"""A deployment can serve a maintained metadata library while keeping its own state.

The experiment state root and the metadata library have separate owners: pointing the
gateway at an existing library must not move sessions, imports or recordings, and an
unset variable must keep every deployment isolated.
"""

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.catalog_metadata import MetadataStore

REVISION = "c" * 40
QUERY = {"scope": "online", "limit": 24, "page": 1, "view": "cards"}


def publish_library(root, count=3):
    """Build a real store through the public API, not a stubbed reader."""
    store = MetadataStore(root)
    entries = [
        {
            "upstreamId": f"tool-{index}",
            "name": {"en": f"Fixture tool {index}", "zh": f"夹具工具 {index}"},
            "summary": {"en": "Synthetic object.", "zh": "合成夹具。"},
            "facts": {"dimensionsM": None, "sourceBytes": None},
            "preview": {"state": "missing", "path": None, "role": None},
        }
        for index in range(count)
    ]
    inventory = store.stage_inventory("simready", REVISION, entries, complete=True)
    release = store.publish([inventory["id"]])
    store.close()
    return release


def settings_for(tmp_path, **fields):
    return Settings(
        state_root=fields.pop("state_root", tmp_path / "state"),
        recordings_root=fields.pop("recordings_root", tmp_path / "recordings"),
        **fields,
    )


def test_explicit_metadata_root_serves_the_existing_library_and_leaves_state_alone(tmp_path):
    state_a = tmp_path / "state-a"
    library_b = tmp_path / "library-b"
    publish_library(library_b)
    app = create_app(
        settings=settings_for(tmp_path, state_root=state_a, catalog_metadata_root=library_b),
        metadata_launch_workers=False,
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/library/search", params=QUERY).json()
        assert len(body["results"]) == 3
        assert body["snapshot"]["id"]
        names = {item["names"]["en"] for item in body["results"]}
        assert names == {"Fixture tool 0", "Fixture tool 1", "Fixture tool 2"}
        # The state root stays the experiment owner's; no second library appears under it.
        assert not (state_a / "catalog-metadata").exists()
        assert (library_b / "metadata.sqlite3").is_file()


def test_the_published_library_survives_a_gateway_restart(tmp_path):
    library_b = tmp_path / "library-b"
    publish_library(library_b)
    settings = settings_for(tmp_path, catalog_metadata_root=library_b)
    app = create_app(settings=settings, metadata_launch_workers=False)
    with TestClient(app) as client:
        body = client.get("/api/v1/library/search", params=QUERY).json()
        assert body["snapshot"]["entryCount"] == 3
        pinned = body["snapshot"]["id"]
    restarted = create_app(settings=settings, metadata_launch_workers=False)
    with TestClient(restarted) as client:
        body = client.get("/api/v1/library/search", params=QUERY).json()
        assert len(body["results"]) == 3, "a restart must still read the release"
        replay = client.get("/api/v1/library/search", params={**QUERY, "release": pinned})
        assert replay.status_code == 200, replay.text
        assert len(replay.json()["results"]) == 3, "a restart must still read the pinned release"
        assert replay.json()["snapshot"]["id"] == pinned


def test_unset_metadata_root_keeps_deployments_isolated(tmp_path):
    state = tmp_path / "state"
    app = create_app(settings=settings_for(tmp_path), metadata_launch_workers=False)
    with TestClient(app) as client:
        # Without a publication the gateway still serves the lightweight directory.
        assert client.get("/api/v1/library/search", params=QUERY).status_code == 200
    store = MetadataStore(state / "catalog-metadata")
    assert store.latest_release() is None
    store.close()
    assert not (tmp_path / "library-b").exists()


def test_a_missing_configured_directory_fails_before_creating_a_second_library(tmp_path):
    missing = tmp_path / "typo" / "catalog-metadata"
    with pytest.raises(RuntimeError, match="SPATIAL_CATALOG_METADATA_ROOT"):
        create_app(
            settings=settings_for(tmp_path, catalog_metadata_root=missing),
            metadata_launch_workers=False,
        )
    assert not missing.exists(), "a typo must not silently create an empty library"


def test_only_one_host_owns_a_library(tmp_path):
    library_b = tmp_path / "library-b"
    publish_library(library_b)
    settings = settings_for(tmp_path, catalog_metadata_root=library_b)
    first = create_app(settings=settings, metadata_launch_workers=False)
    with TestClient(first):
        second = create_app(settings=settings, metadata_launch_workers=False)
        with pytest.raises(Exception, match="already hosted|owns this state root"):
            with TestClient(second):
                pass
