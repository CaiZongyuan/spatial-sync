"""External Authoring Reports: idempotency, binding, bounds and platform isolation."""

import hashlib
import json
import re
import shutil
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app

NAME = "cart-fixture"
SCENE = f"{NAME}@2"
ROOT = Path(__file__).resolve().parents[2]


def scene_package(root):
    """A user-library v2 with a real platform failure, distinct from built-in scenes."""
    destination = root / f"scenes/{NAME}/2"
    shutil.copytree(ROOT / "library/scenes/lab-demo/1", destination)
    metadata = yaml.safe_load((destination / "scene.yaml").read_text(encoding="utf-8"))
    metadata |= {"name": NAME, "title": "Cart isolation fixture"}
    (destination / "scene.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    bindings = yaml.safe_load((destination / "devices.yaml").read_text(encoding="utf-8"))
    bindings["scene"] = NAME
    (destination / "devices.yaml").write_text(yaml.safe_dump(bindings), encoding="utf-8")
    (destination / "validation.json").write_text(json.dumps({
        "schema": "lw.validation/1",
        "subject": f"scene:{SCENE}",
        "ranAt": "2026-10-24T10:00:00+00:00",
        "toolVersions": {},
        "checks": [{
            "id": "P3", "tier": "physics", "status": "fail",
            "message": "台车位移 0.00 m，超过 0.10 m 上限",
            "entityKey": "/World/Lab/Cart",
        }],
        "launchable": False,
    }, ensure_ascii=False), encoding="utf-8")
    return destination


def report(content_hash, **overrides):
    value = {
        "submissionId": "sub-cart-edit",
        "source": "claude-code/scene-director",
        "sourceVersion": "1.4.0",
        "reportedAt": "2026-10-24T12:00:00+00:00",
        "draftId": "blender-cart-draft",
        "object": {"kind": "scene", "id": SCENE, "contentHash": content_hash},
        "summary": "Blender 工作文件把台车移到 0.20 m；尚未导出或提交。",
        "gaps": ["新包未导出", "未提交新的 Scene Version"],
        "artifacts": [{"name": "preview", "reference": "artifact://cart-draft/preview.png"}],
    }
    return value | overrides


def make_client(tmp_path):
    scene_package(tmp_path / "user-library")
    client = TestClient(create_app())
    detail = client.get(f"/api/v1/library/scenes/{NAME}/2").json()
    return client, detail


def stored_path(tmp_path, submission_id):
    digest = hashlib.sha256(submission_id.encode("utf-8")).hexdigest()
    return tmp_path / "state/authoring/reports" / f"{digest}.json"


def test_submission_is_idempotent_persistent_and_readable(tmp_path):
    client, scene = make_client(tmp_path)
    with client:
        body = report(scene["contentHash"])
        first = client.post("/api/v1/library/authoring-reports", json=body)
        assert first.status_code == 201, first.text
        stored = first.json()
        assert stored["schema"] == "lw.authoring-report/1"
        assert stored["submissionId"] == body["submissionId"]
        assert stored["source"] == body["source"]
        assert stored["sourceVersion"] == body["sourceVersion"]
        assert stored["reportedAt"] == "2026-10-24T12:00:00+00:00"
        assert stored["receivedAt"] == client.app.state.authoring_reports.show(
            body["submissionId"]
        )["receivedAt"]
        assert stored["draftId"] == "blender-cart-draft"
        assert stored["object"] == body["object"]
        assert stored["summaryBytes"] == len(body["summary"].encode("utf-8"))
        persisted = stored_path(tmp_path, "sub-cart-edit")
        assert json.loads(persisted.read_text(encoding="utf-8")) == stored

        # The submissionId is the idempotency key: a replay never rewrites the receipt,
        # even when the caller now sends a different summary.
        replay = client.post("/api/v1/library/authoring-reports", json=body)
        assert replay.status_code == 200 and replay.json() == stored
        conflicting = client.post(
            "/api/v1/library/authoring-reports",
            json=body | {"summary": "A later local edit", "gaps": []},
        )
        assert conflicting.status_code == 200 and conflicting.json() == stored
        assert json.loads(persisted.read_text(encoding="utf-8")) == stored

        assert client.get("/api/v1/library/authoring-reports/sub-cart-edit").json() == stored
        listing = client.get(
            "/api/v1/library/authoring-reports", params={"scene": SCENE}
        ).json()
        assert listing["reports"] == [{
            "schema": stored["schema"], "submissionId": stored["submissionId"],
            "source": stored["source"], "sourceVersion": stored["sourceVersion"],
            "reportedAt": stored["reportedAt"], "receivedAt": stored["receivedAt"],
            "draftId": stored["draftId"], "object": stored["object"],
            "summaryPreview": stored["summary"], "summaryBytes": stored["summaryBytes"],
            "summaryTruncated": False, "gapCount": 2,
            "gapsPreview": stored["gaps"], "artifactCount": 1,
        }]
        assert client.get(
            "/api/v1/library/authoring-reports", params={"draft": "blender-cart-draft"}
        ).json()["reports"] == listing["reports"]
        assert client.get(
            "/api/v1/library/authoring-reports", params={"scene": "other-scene"}
        ).json() == {"reports": []}


def test_binding_requires_a_draft_or_an_exact_existing_version_hash(tmp_path):
    client, scene = make_client(tmp_path)
    with client:
        asset = client.get("/api/v1/library/assets/robot/fr3/1").json()
        exact_asset = client.post("/api/v1/library/authoring-reports", json=report(
            scene["contentHash"], submissionId="sub-asset",
            object={"kind": "asset", "id": "robot/fr3@1", "contentHash": asset["contentHash"]},
            draftId=None,
        ))
        assert exact_asset.status_code == 201
        assert exact_asset.json()["object"]["id"] == "robot/fr3@1"

        draft_only = client.post("/api/v1/library/authoring-reports", json=report(
            scene["contentHash"], submissionId="sub-draft-only", object=None,
        ))
        assert draft_only.status_code == 201 and draft_only.json()["object"] is None

        for document in (
            report(scene["contentHash"], submissionId="sub-none", draftId=None, object=None),
            report("b" * 64, submissionId="sub-wrong-hash"),
            report(scene["contentHash"], submissionId="sub-unknown", object={
                "kind": "scene", "id": "cart-fixture@9", "contentHash": scene["contentHash"],
            }),
            report(scene["contentHash"], submissionId="sub-inexact", object={
                "kind": "scene", "id": "cart-fixture", "contentHash": scene["contentHash"],
            }),
            report(scene["contentHash"], submissionId="sub-alias", object={
                "kind": "scene", "id": "cart-fixture@02", "contentHash": scene["contentHash"],
            }),
        ):
            rejected = client.post("/api/v1/library/authoring-reports", json=document)
            assert rejected.status_code == 422, (document["submissionId"], rejected.text)
            assert rejected.json()["detail"]["code"] == "invalid_authoring_report"


def test_summary_bounds_plain_text_and_no_attachment_or_path_fields(tmp_path):
    client, scene = make_client(tmp_path)
    with client:
        exact = "x" * (64 * 1024)
        assert client.post("/api/v1/library/authoring-reports", json=report(
            scene["contentHash"], submissionId="sub-exact-summary", summary=exact,
        )).status_code == 201
        preview = client.get(
            "/api/v1/library/authoring-reports",
            params={"scene": SCENE, "limit": 1},
        ).json()["reports"][0]
        assert preview["summaryBytes"] == len(exact.encode("utf-8"))
        assert preview["summaryTruncated"] is True
        assert len(preview["summaryPreview"]) == 2000

        for document in (
            report(scene["contentHash"], submissionId="sub-big", summary="x" * (64 * 1024 + 1)),
            report(scene["contentHash"], submissionId="sub-multibyte", summary="汉" * 22000),
            report(scene["contentHash"], submissionId="sub-nul", summary="ok\x00no"),
            report(scene["contentHash"], submissionId="sub-gaps", gaps=["x"] * 101),
            report(scene["contentHash"], submissionId="sub-gap", gaps=["x" * 2049]),
            report(scene["contentHash"], submissionId="sub-artifacts",
                   artifacts=[{"name": "a", "reference": "b"}] * 51),
            report(scene["contentHash"], submissionId="sub-artifact-name",
                   artifacts=[{"name": "n" * 129, "reference": "artifact://ok"}]),
            report(scene["contentHash"], submissionId="sub-artifact-ref",
                   artifacts=[{"name": "a", "reference": "r" * 1025}]),
            report(scene["contentHash"], submissionId="sub-attachment",
                   attachments=[{"name": "scene.blend", "content": "AA=="}]),
            report(scene["contentHash"], submissionId="sub-path", path="C:\\secret\\scene.blend"),
            report(scene["contentHash"], submissionId="sub-received", receivedAt="2026-10-25T00:00:00Z"),
            report(scene["contentHash"], submissionId="../escape"),
            report(scene["contentHash"], submissionId="sub-source", source=""),
        ):
            rejected = client.post("/api/v1/library/authoring-reports", json=document)
            assert rejected.status_code == 422, (document["submissionId"], rejected.text)
            assert rejected.json()["detail"]["code"] == "invalid_authoring_report"

        assert client.get("/api/v1/library/authoring-reports/unknown").status_code == 404
        assert client.get(
            "/api/v1/library/authoring-reports/unknown"
        ).json()["detail"]["code"] == "unknown_authoring_report"
        assert client.get("/api/v1/library/authoring-reports/..%2Fescape").status_code == 404
        for limit in ("0", "101", "ten"):
            rejected = client.get(
                "/api/v1/library/authoring-reports", params={"limit": limit}
            )
            assert rejected.status_code == 422
            assert rejected.json()["detail"]["code"] == "invalid_authoring_report"
        assert client.post("/api/v1/library/authoring-reports", content=b"[]").status_code == 422


def test_late_and_old_reports_stay_visible_and_never_replace_validation(tmp_path):
    client, scene = make_client(tmp_path)
    with client:
        before = scene["validation"]
        later = client.post("/api/v1/library/authoring-reports", json=report(
            scene["contentHash"], submissionId="sub-later",
            reportedAt="2026-10-24T12:00:00+00:00",
        ))
        older = client.post("/api/v1/library/authoring-reports", json=report(
            scene["contentHash"], submissionId="sub-older",
            reportedAt="2026-10-20T08:00:00+00:00", draftId="older-draft", object=None,
            summary="Older draft note", gaps=[],
        ))
        assert later.status_code == 201 and older.status_code == 201
        listed = client.get(
            "/api/v1/library/authoring-reports", params={"scene": SCENE}
        ).json()["reports"]
        assert [item["submissionId"] for item in listed] == ["sub-later"]
        all_reports = client.get("/api/v1/library/authoring-reports").json()["reports"]
        assert [item["submissionId"] for item in all_reports] == ["sub-later", "sub-older"]
        assert all_reports[1]["reportedAt"] == "2026-10-20T08:00:00+00:00"
        assert all_reports[1]["receivedAt"]
        assert client.get("/api/v1/library/authoring-reports/sub-older").json()["summary"] == (
            "Older draft note"
        )

        after = client.get(f"/api/v1/library/scenes/{NAME}/2").json()
        assert after["validation"] == before
        assert after["validationStatus"] == "available"
        assert after["launchable"] is False
        assert after["launchReason"] and "0.00 m" in after["launchReason"]
        assert json.loads(
            stored_path(tmp_path, "sub-later").read_text(encoding="utf-8")
        )["summary"].startswith("Blender")
        metadata = yaml.safe_load(
            (tmp_path / f"user-library/scenes/{NAME}/2/scene.yaml").read_text(encoding="utf-8")
        )
        assert metadata["name"] == NAME


def test_storage_names_are_portable_and_ids_stay_case_sensitive(tmp_path):
    client, scene = make_client(tmp_path)
    with client:
        colon = report(scene["contentHash"], submissionId="sub:cart", summary="Colon id")
        assert client.post(
            "/api/v1/library/authoring-reports", json=colon
        ).status_code == 201
        assert client.get(
            "/api/v1/library/authoring-reports/sub:cart"
        ).json()["summary"] == "Colon id"

        upper = report(scene["contentHash"], submissionId="Sub-1", summary="Upper id")
        lower = report(scene["contentHash"], submissionId="sub-1", summary="Lower id")
        assert client.post(
            "/api/v1/library/authoring-reports", json=upper
        ).status_code == 201
        assert client.post(
            "/api/v1/library/authoring-reports", json=lower
        ).status_code == 201
        assert client.get(
            "/api/v1/library/authoring-reports/Sub-1"
        ).json()["summary"] == "Upper id"
        assert client.get(
            "/api/v1/library/authoring-reports/sub-1"
        ).json()["summary"] == "Lower id"

        paths = sorted((tmp_path / "state/authoring/reports").glob("*.json"))
        assert len(paths) == 3
        assert all(re.fullmatch(r"[0-9a-f]{64}\.json", path.name) for path in paths)
        assert not any(":" in path.name for path in paths)

    # A restart keeps each receipt addressable without a case-insensitive collision.
    with TestClient(create_app()) as restarted:
        assert restarted.get(
            "/api/v1/library/authoring-reports/Sub-1"
        ).json()["summary"] == "Upper id"
        replay = restarted.post("/api/v1/library/authoring-reports", json=upper)
        assert replay.status_code == 200
        assert replay.json()["summary"] == "Upper id"


def test_list_sorts_absolute_times_across_offsets(tmp_path):
    client, scene = make_client(tmp_path)
    with client:
        earlier = report(
            scene["contentHash"], submissionId="sub-earlier",
            reportedAt="2026-10-24T12:00:00+08:00",
        )
        later = report(
            scene["contentHash"], submissionId="sub-later-absolute",
            reportedAt="2026-10-24T05:00:00+00:00",
        )
        assert client.post(
            "/api/v1/library/authoring-reports", json=earlier
        ).status_code == 201
        assert client.post(
            "/api/v1/library/authoring-reports", json=later
        ).status_code == 201
        listed = client.get("/api/v1/library/authoring-reports").json()["reports"]
        assert [item["submissionId"] for item in listed] == [
            "sub-later-absolute", "sub-earlier",
        ]


def test_authoring_body_cap_holds_for_chunked_requests(tmp_path):
    client, _ = make_client(tmp_path)

    def chunks():
        yield b'{"submissionId":"sub-chunked","source":"agent","sourceVersion":"1",'
        yield b'"reportedAt":"2026-10-24T12:00:00+00:00","draftId":"draft","summary":"'
        for _ in range(20):
            yield b"x" * 65536
        yield b'"}'

    with client:
        response = client.post(
            "/api/v1/library/authoring-reports",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "invalid_authoring_report"
