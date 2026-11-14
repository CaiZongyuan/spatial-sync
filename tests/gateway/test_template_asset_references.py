"""The delivered asset API discovers every real migrated template dependency without a Source."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.sources.newton_source import NewtonSource
from apps.api.app.sources.synthetic_source import SyntheticSource


def test_migrated_scene_dependencies_and_asset_reverse_references_agree_without_running_a_source():
    with TestClient(create_app()) as client:
        with (
            patch.object(SyntheticSource, "start", side_effect=AssertionError("GET started Synthetic")),
            patch.object(NewtonSource, "start", side_effect=AssertionError("GET started Newton")),
        ):
            scenes = {
                f"{name}@{rev}": client.get(f"/api/v1/library/scenes/{name}/{rev}").json()
                for name, rev in (
                    ("lab-demo", 3),
                    ("robot-and-tubes", 2),
                    ("microduck-arena", 1),
                    ("microduck-arena", 2),
                    ("microduck-lab-patrol", 1),
                    ("microduck-lab-patrol", 2),
                )
            }
            expected = {}
            for key, scene in scenes.items():
                expected_status = "not_required" if key == "microduck-arena@2" else "resolved"
                assert scene["templateResolution"]["status"] == expected_status and scene["launchable"]
                for dependency in scene["assets"]:
                    expected.setdefault(dependency["id"], []).append(
                        (scene["sceneVersion"], dependency["contentHash"])
                    )
            assert len(expected) == 10
            for identifier, references in expected.items():
                bare, revision = identifier.split("@")
                response = client.get(f"/api/v1/library/assets/{bare}/{revision}")
                assert response.status_code == 200, response.text
                asset = response.json()
                assert asset["referenceInspection"]["complete"], asset["referenceInspection"]
                assert {row["id"] for row in asset["referencingScenes"]} == {scene for scene, _ in references}
                assert all(digest == asset["contentHash"] for _, digest in references)
                assert asset["implementedTypes"]
            assert client.get("/api/v1/health").json()["sessions"] == []
