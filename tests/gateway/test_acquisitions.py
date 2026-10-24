"""Public acquisition journey: start, progress, result, cancel, retry and restart."""

import asyncio
import json
import os
import shutil
import threading
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.acquisition import Limits
from packages.acquisition.runner import AcquisitionCancelled, AcquisitionFailure, StagedResult

ROOT = Path(__file__).resolve().parents[2]
REVISION = "524747d9b5e1f21b5b18b56041788c01d745f1a1"
BOX = f"simready/box_cardboard_a01@{REVISION}"
CART = f"simready/equipment_utility_cart_e01@{REVISION}"
STAGE = (
    b'#usda 1.0\n(defaultPrim = "Asset"\n upAxis = "Z"\n metersPerUnit = 1)\n'
    b'def Xform "Asset" {}\n'
)


def write_package(stage):
    stage.mkdir(parents=True)
    (stage / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "environment/cardboard-box",
                "kind": "environment",
                "title": "Cardboard box",
                "stage": "asset.usda",
                "dimensionsM": [0.7, 0.52, 0.51],
                "dressing": True,
                "physics": {"massKg": 1.2, "collision": "authored"},
            }
        ),
        encoding="utf-8",
    )
    (stage / "asset.usda").write_bytes(STAGE)
    (stage / "LICENSE.txt").write_text("CC-BY-4.0\n", encoding="utf-8")
    (stage / "provenance.json").write_text(
        json.dumps(
            {
                "sourcePackage": "box_cardboard_a01",
                "sourceRevision": REVISION,
                "license": {"name": "CC-BY-4.0", "admitted": True},
            }
        ),
        encoding="utf-8",
    )


class FakeRunner:
    def __init__(self, *, failures=()):
        self.failures = list(failures)

    @property
    def staging_root(self):
        return Path(os.environ["LW_LIBRARY"]) / ".acquisition-staging"

    async def run(self, record, progress, cancel):
        total = record.estimate["source"]["bytes"]
        files = record.estimate["source"]["files"]
        await progress(
            {
                "phase": "downloading",
                "completedBytes": total // 2,
                "totalBytes": total,
                "completedFiles": 1,
                "totalFiles": files,
            }
        )
        await asyncio.sleep(0.02)
        if cancel.is_set():
            raise AcquisitionCancelled()
        if self.failures:
            raise self.failures.pop(0)
        await progress(
            {
                "phase": "preparing",
                "completedBytes": total,
                "totalBytes": total,
                "completedFiles": files,
                "totalFiles": files,
            }
        )
        stage = self.staging_root / record.id / "package"
        write_package(stage)
        return StagedResult(stage, usage={"adaptedBytes": 32})


class GateRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.gate = threading.Event()

    async def run(self, record, progress, cancel):
        await progress({"phase": "downloading", "completedBytes": 1, "totalBytes": 10})
        while not self.gate.is_set():
            if cancel.is_set():
                raise AcquisitionCancelled()
            await asyncio.sleep(0.01)
        return await FakeRunner.run(self, record, progress, cancel)


def wait(client, identifier, statuses=("succeeded", "failed", "cancelled"), timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        document = client.get(f"/api/v1/library/acquisitions/{identifier}").json()
        if document.get("status") in statuses:
            return document
        time.sleep(0.02)
    raise AssertionError(f"Acquisition did not finish: {document}")


def start(client, ref=BOX):
    response = client.post("/api/v1/library/acquisitions", json={"ref": ref})
    return response


def test_start_progress_result_list_show_and_offline_local_asset():
    with TestClient(create_app(acquisition_runner=FakeRunner())) as client:
        response = start(client)
        assert response.status_code == 202, response.text
        document = response.json()
        assert client.app.state.acquisitions is not None
        assert document["schema"] == "lw.acquisition/1"
        assert document["status"] == "queued"
        assert document["estimate"]["cache"]["known"] is True
        assert document["estimate"]["withinLimits"] is True
        assert "url" not in document and "path" not in document
        final = wait(client, document["id"])
        assert final["status"] == "succeeded", final
        assert final["progress"]["totalBytes"] == 21530693
        assert final["progress"]["completedBytes"] == 21530693
        result = final["result"]
        assert result["assetVersion"] == "environment/cardboard-box@1"
        assert result["validation"]["status"] in ("launchable", "gaps")
        assert final["usage"]["adaptedBytes"] == 32
        assert final["budget"]["releasedAt"] is not None
        assert client.get(f"/api/v1/library/acquisitions/{document['id']}").json() == final
        listing = client.get("/api/v1/library/acquisitions").json()["acquisitions"]
        assert [item["id"] for item in listing] == [document["id"]]
        assert client.get(
            "/api/v1/library/acquisitions", params={"status": "succeeded"}
        ).json()["acquisitions"][0]["id"] == document["id"]
        assert client.get("/api/v1/library/acquisitions", params={"limit": 51}).status_code == 422

        detail = client.get("/api/v1/library/assets/environment/cardboard-box/1")
        assert detail.status_code == 200 and detail.json()["contentHash"] == result["contentHash"]
        cache_root = Path(os.environ["SPATIAL_STATE_ROOT"]) / "acquisitions/cache"
        shutil.rmtree(cache_root, ignore_errors=True)
        assert not cache_root.exists()
        offline = client.get("/api/v1/library/assets/environment/cardboard-box/1").json()
        for url in offline["files"].values():
            assert client.get(url).status_code == 200


def test_ineligible_unknown_and_no_path_or_url_inputs():
    with TestClient(create_app(acquisition_runner=FakeRunner())) as client:
        ineligible = start(client, CART)
        assert ineligible.status_code == 409
        detail = ineligible.json()["detail"]
        assert detail["code"] == "acquisition_ineligible"
        assert detail["reasons"] and detail["estimate"] and detail["limits"]
        assert client.get("/api/v1/library/acquisitions").json()["acquisitions"] == []

        unknown = start(client, f"simready/absent_package@{REVISION}")
        assert unknown.status_code == 404
        assert unknown.json()["detail"]["code"] == "unknown_catalog_entry"

        for ref in (
            "https://huggingface.co/datasets/nvidia/simready-assets",
            "C:\\assets\\box",
            "../outside",
            "simready/box_cardboard_a01",
        ):
            rejected = start(client, ref)
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["detail"]["code"] == "invalid_acquisition"

        for body in (
            {"path": str(ROOT)},
            {"url": "https://example.invalid/box"},
            {"ref": BOX, "url": "https://example.invalid/box"},
            {},
        ):
            rejected = client.post("/api/v1/library/acquisitions", json=body)
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["detail"]["code"] == "invalid_acquisition"

        assert client.get("/api/v1/library/acquisitions/acq-" + "0" * 32).status_code == 404
        assert client.get("/api/v1/library/acquisitions/acq-" + "0" * 32).json()["detail"][
            "code"
        ] == "unknown_acquisition"
        assert client.get("/api/v1/library/acquisitions/nope").status_code == 422
        assert client.get(
            "/api/v1/library/acquisitions", params={"status": "broken"}
        ).status_code == 422


def test_budget_exceeded_returns_estimate_limits_and_reasons():
    with TestClient(
        create_app(acquisition_runner=FakeRunner(), acquisition_limits=Limits(batch_bytes=1))
    ) as client:
        exceeded = start(client)
        assert exceeded.status_code == 409
        detail = exceeded.json()["detail"]
        assert detail["code"] == "budget_exceeded"
        assert detail["reasons"] and detail["estimate"]["withinLimits"] is False
        assert detail["limits"]["batchBytes"] == 1
        assert client.get("/api/v1/library/acquisitions").json()["acquisitions"] == []


def test_injected_disk_probe_is_used_by_preflight():
    with TestClient(
        create_app(acquisition_runner=FakeRunner(), acquisition_disk_usage=lambda: 1024)
    ) as client:
        exceeded = start(client)
        assert exceeded.status_code == 409
        assert exceeded.json()["detail"]["code"] == "budget_exceeded"
        assert any("Free disk" in item for item in exceeded.json()["detail"]["reasons"])


def test_cancel_then_retry_publishes_with_a_new_attempt():
    runner = GateRunner()
    with TestClient(create_app(acquisition_runner=runner)) as client:
        document = start(client).json()
        assert wait(client, document["id"], statuses=("downloading",))["status"] == "downloading"
        cancelled = client.post(f"/api/v1/library/acquisitions/{document['id']}/cancel").json()
        assert cancelled["cancelRequested"] is True
        final = wait(client, document["id"])
        assert final["status"] == "cancelled" and final["error"] is None
        assert not (Path(os.environ["LW_LIBRARY"]) / "assets/environment").exists()

        runner.gate.set()
        retried = client.post(f"/api/v1/library/acquisitions/{document['id']}/retry").json()
        assert retried["attempt"] == 2 and retried["status"] == "queued"
        assert retried["budget"]["reservationId"].endswith("@2")
        succeeded = wait(client, document["id"])
        assert succeeded["status"] == "succeeded"
        assert succeeded["result"]["assetVersion"] == "environment/cardboard-box@1"
        assert client.post(f"/api/v1/library/acquisitions/{document['id']}/retry").status_code == 422


def test_failed_attempt_can_be_retried_and_reports_a_structured_error():
    runner = FakeRunner(
        failures=[AcquisitionFailure("source_unavailable", "fixture source unavailable", True)]
    )
    with TestClient(create_app(acquisition_runner=runner)) as client:
        document = start(client).json()
        failed = wait(client, document["id"])
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "source_unavailable"
        assert failed["error"]["retryable"] is True
        retried = client.post(f"/api/v1/library/acquisitions/{document['id']}/retry")
        assert retried.status_code == 200 and retried.json()["attempt"] == 2
        assert wait(client, document["id"])["status"] == "succeeded"


def test_three_starts_run_two_and_queue_one():
    runner = GateRunner()
    with TestClient(create_app(acquisition_runner=runner)) as client:
        identifiers = [start(client).json()["id"] for _ in range(3)]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            statuses = [
                client.get(f"/api/v1/library/acquisitions/{identifier}").json()["status"]
                for identifier in identifiers
            ]
            if statuses.count("downloading") == 2:
                break
            time.sleep(0.02)
        assert statuses.count("downloading") == 2, statuses
        assert statuses.count("queued") == 1, statuses
        runner.gate.set()
        documents = [wait(client, identifier) for identifier in identifiers]
        assert [item["status"] for item in documents] == ["succeeded"] * 3
        assert [item["result"]["deduplicated"] for item in documents].count(False) == 1


def test_restart_marks_active_work_interrupted_and_retry_is_explicit():
    with TestClient(create_app(acquisition_runner=GateRunner())) as client:
        document = start(client).json()
        assert wait(client, document["id"], statuses=("downloading",))["status"] == "downloading"
    assert not (Path(os.environ["LW_LIBRARY"]) / "assets/environment").exists()
    with TestClient(create_app(acquisition_runner=FakeRunner())) as client:
        interrupted = client.get(
            f"/api/v1/library/acquisitions/{document['id']}"
        ).json()
        assert interrupted["status"] == "failed"
        assert interrupted["error"]["code"] == "interrupted"
        assert interrupted["error"]["retryable"] is True
        retried = client.post(f"/api/v1/library/acquisitions/{document['id']}/retry").json()
        assert retried["attempt"] == 2
        assert wait(client, document["id"])["status"] == "succeeded"
