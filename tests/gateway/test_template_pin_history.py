"""Durable same-version report history cannot forget or contradict a known asset pin."""

import copy
import json

import pytest
import yaml
from fastapi.testclient import TestClient
from template_support import reference_files, scene_files, upload

from apps.api.app.main import create_app


def known_later_dependency(client):
    """Embedded report knows A; the real revalidation sidecar first discovers B."""
    original = upload(client, reference_files()).json()
    files, declarations = scene_files()
    incubator = next(b for b in declarations["entities"] if b.get("type") == "incubator/v1")
    incubator.pop("type")
    incubator.pop("program")
    incubator["template"] = "device/later-reference@1"
    files["devices.yaml"] = yaml.safe_dump(declarations).encode()
    imported = upload(client, files)
    assert imported.status_code == 200, imported.text
    assert imported.json()["validation"]["referencedAssets"] == [
        {"id": original["assetVersion"], "contentHash": original["contentHash"]}
    ]
    later_files = reference_files()
    metadata = yaml.safe_load(later_files["asset.yaml"])
    metadata["id"] = "device/later-reference"
    metadata["implements"] = [{"type": "incubator/v1", "programs": {"synthetic": "incubator.virtual"}}]
    later_files["asset.yaml"] = yaml.safe_dump(metadata).encode()
    later = upload(client, later_files).json()
    checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
    assert checked.status_code == 200 and checked.json()["launchable"], checked.text
    scene = client.app.state.manager.library.resolve("template-slice@1")
    path = client.app.state.library_assets.resolve(later["assetVersion"]).path
    return scene, path, checked.json(), later_files


@pytest.mark.parametrize("changed", [False, True])
def test_restart_uses_a_known_pin_even_when_a_later_legacy_failure_report_forgot_it(tmp_path, changed):
    with TestClient(create_app()) as client:
        scene, path, first, _ = known_later_dependency(client)
        offline = tmp_path / "offline-asset"
        path.rename(offline)
        # Reproduce a valid report produced by an earlier validator, not the new monotone writer.
        scene.reports.save(
            first
            | {
                "launchable": False,
                "checks": [{"id": "S5", "tier": "structural", "status": "fail", "message": "Old failure"}],
                "referencedAssets": scene.validation["referencedAssets"],
            },
            scene.content_hash,
        )
    with TestClient(create_app()) as client:
        missing = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert missing.status_code == 200 and not missing.json()["launchable"]
        assert missing.json()["referencedAssets"] == first["referencedAssets"]
    offline.rename(path)
    if changed:
        (path / "asset.usda").write_bytes((path / "asset.usda").read_bytes() + b"\n")
    with TestClient(create_app()) as client:
        rejected = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert rejected.status_code == 409
        checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert checked.status_code == 200, checked.text
        assert checked.json()["launchable"] is not changed, checked.text
        assert checked.json()["referencedAssets"] == first["referencedAssets"]
        launched = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launched.status_code == (409 if changed else 201), launched.text


def test_conflicting_digests_in_bound_history_are_inspectable_and_cannot_be_revalidated_away():
    with TestClient(create_app()) as client:
        scene, _, first, _ = known_later_dependency(client)
        conflicting = copy.deepcopy(first)
        next(pin for pin in conflicting["referencedAssets"] if pin["id"] == "device/later-reference@1")[
            "contentHash"
        ] = "f" * 64
        scene.reports.save(conflicting, scene.content_hash)
    with TestClient(create_app()) as client:
        detail = client.get("/api/v1/library/scenes/template-slice/1")
        assert detail.status_code == 200 and not detail.json()["launchable"], detail.text
        assert detail.json()["templateResolution"]["status"] == "failed"
        assert "Conflicting recorded asset hashes" in detail.json()["templateResolution"]["error"]
        checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert checked.status_code == 409, checked.text
        assert checked.json()["detail"]["code"] == "scene_not_launchable"
        launched = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launched.status_code == 409
        assert client.get("/api/v1/health").json()["sessions"] == []


@pytest.mark.parametrize("other", ["subject", "hash", "misbound-subject", "misbound-hash", "new-revision"])
def test_other_scene_identity_or_asset_revision_does_not_contaminate_this_scenes_pins(other):
    with TestClient(create_app()) as client:
        scene, _, first, later_files = known_later_dependency(client)
        if other == "new-revision":
            metadata = yaml.safe_load(later_files["asset.yaml"])
            metadata["title"] = "New revision, same first revision identity retained"
            later_files["asset.yaml"] = yaml.safe_dump(metadata).encode()
            imported = upload(client, later_files)
            assert (
                imported.status_code == 200 and imported.json()["assetVersion"] == "device/later-reference@2"
            )
        else:
            foreign = copy.deepcopy(first)
            next(pin for pin in foreign["referencedAssets"] if pin["id"] == "device/later-reference@1")[
                "contentHash"
            ] = "f" * 64
            foreign["subject"] = "scene:other@1" if "subject" in other else foreign["subject"]
            foreign_hash = "e" * 64 if "hash" in other else scene.content_hash
            saved = scene.reports.save(foreign, foreign_hash)
            if other.startswith("misbound-"):
                # A filename/directory alone does not make a receipt belong to this version.
                directory = scene.reports.directory("scene:" + scene.id, scene.content_hash)
                (directory / (saved["reportId"] + ".json")).write_text(json.dumps(saved), encoding="utf-8")
    with TestClient(create_app()) as client:
        checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert checked.status_code == 200 and checked.json()["launchable"], checked.text
        assert checked.json()["referencedAssets"] == first["referencedAssets"]
        launched = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launched.status_code == 201, launched.text
