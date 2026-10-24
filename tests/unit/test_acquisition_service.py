"""Acquisition records, budgets, cache LRU and the persistent lifecycle service."""

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import pytest
import yaml

from packages.acquisition import AcquisitionService, Limits, cache
from packages.acquisition.budget import BudgetExceeded, BudgetLedger
from packages.acquisition.models import AcquisitionRecord, source_document
from packages.acquisition.runner import (
    AcquisitionCancelled,
    AcquisitionFailure,
    StagedResult,
    SubprocessRunner,
)
from packages.catalog import AssetCatalog
from packages.library.assets import AssetLibrary
from packages.library.common import LibraryError

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "catalog/simready.json"
REVISION = "524747d9b5e1f21b5b18b56041788c01d745f1a1"
BOX = f"simready/box_cardboard_a01@{REVISION}"
BOX_REF = f"catalog:{BOX}"
CART = f"simready/equipment_utility_cart_e01@{REVISION}"
STAGE = (
    b'#usda 1.0\n(defaultPrim = "Asset"\n upAxis = "Z"\n metersPerUnit = 1)\n'
    b'def Xform "Asset" {}\n'
)


def write_package(stage, name="cardboard-box"):
    stage.mkdir(parents=True)
    (stage / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": f"environment/{name}",
                "kind": "environment",
                "title": name.replace("-", " ").title(),
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
    def __init__(self, library_root, *, failures=(), delay=0.0, name="cardboard-box"):
        self.staging_root = Path(library_root) / ".acquisition-staging"
        self.failures = list(failures)
        self.delay = delay
        self.name = name
        self.runs = 0

    async def run(self, record, progress, cancel):
        self.runs += 1
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
        deadline = time.monotonic() + self.delay
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise AcquisitionCancelled()
            await asyncio.sleep(0.01)
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
        write_package(stage, self.name)
        return StagedResult(stage, usage={"adaptedBytes": 512})


class GateRunner(FakeRunner):
    def __init__(self, library_root, **kwargs):
        super().__init__(library_root, **kwargs)
        self.gate = asyncio.Event()
        self.entered = 0
        self.two_started = asyncio.Event()

    async def run(self, record, progress, cancel):
        self.entered += 1
        if self.entered >= 2:
            self.two_started.set()
        await progress({"phase": "downloading", "completedBytes": 1, "totalBytes": 10})
        while not self.gate.is_set():
            if cancel.is_set():
                raise AcquisitionCancelled()
            await asyncio.sleep(0.01)
        return await FakeRunner.run(self, record, progress, cancel)


def build(tmp_path, runner=None, **kwargs):
    library = library_at(tmp_path)
    catalog = AssetCatalog(CATALOG_PATH)
    catalog.startup()
    assert catalog.available
    service = AcquisitionService(
        tmp_path / "state",
        tmp_path / "library",
        library,
        catalog,
        runner=runner,
        **kwargs,
    )
    return service, library, catalog


def library_at(tmp_path):
    library = AssetLibrary(
        tmp_path / "state", builtin_root=tmp_path / "builtin", user_root=tmp_path / "library"
    )
    library.startup()
    return library


async def terminal(service, identifiers, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        documents = [service.show(identifier) for identifier in identifiers]
        if all(item["status"] in ("succeeded", "failed", "cancelled") for item in documents):
            return documents
        await asyncio.sleep(0.02)
    raise AssertionError(f"Acquisition did not finish: {[service.show(i) for i in identifiers]}")


def error_of(call):
    with pytest.raises(LibraryError) as caught:
        call()
    return caught.value


async def catch(awaitable):
    with pytest.raises(LibraryError) as caught:
        await awaitable
    return caught.value


async def test_success_progress_result_usage_and_persisted_record(tmp_path):
    runner = FakeRunner(tmp_path / "library")
    service, library, _ = build(tmp_path, runner)
    await service.startup()
    started = await service.start(BOX_REF)
    assert started["status"] == "queued" and started["source"]["recipe"] == (
        "simready-newton-usdpreview-v1"
    )
    # The disk hold is the space the attempt consumes, not the free-disk reserve.
    assert started["budget"]["reservedDiskBytes"] == 21530693 + 19085294
    assert started["budget"]["sourceBytes"] == 21530693
    assert started["estimate"]["requiredDiskBytes"] == (
        21530693 + 19085294 + Limits().free_disk_min_bytes
    )
    document = (await terminal(service, [started["id"]]))[0]
    assert document["status"] == "succeeded", document
    assert document["progress"]["totalBytes"] == 21530693
    assert document["progress"]["completedBytes"] == 21530693
    assert document["progress"]["completedFiles"] == 36
    assert document["progress"]["phase"] == "importing"
    result = document["result"]
    assert result["assetVersion"] == "environment/cardboard-box@1"
    assert len(result["contentHash"]) == 64
    assert result["deduplicated"] is False
    assert result["validation"]["status"] in ("launchable", "gaps")
    assert isinstance(result["validation"]["gapCount"], int)
    assert "dressing" in result["usage"] and "staticCollision" in result["usage"]
    assert document["usage"]["adaptedBytes"] == 512
    assert document["budget"]["releasedAt"] is not None
    stored = json.loads(
        (tmp_path / "state/acquisitions/records" / f"{document['id']}.json").read_text(
            encoding="utf-8"
        )
    )
    assert stored == document
    assert AcquisitionRecord.from_document(stored).document() == stored
    assert library.resolve("environment/cardboard-box@1").content_hash == result["contentHash"]
    assert service.list()["acquisitions"][0]["id"] == document["id"]


async def test_progress_updates_are_throttled_but_phase_changes_persist(tmp_path):
    runner = FakeRunner(tmp_path / "library")
    service, _, _ = build(tmp_path, runner, progress_interval=60)
    await service.startup()
    document = (await terminal(service, [(await service.start(BOX_REF))["id"]]))[0]
    # The second progress call changed the phase, so it must be durable.
    assert document["progress"]["completedBytes"] == 21530693
    assert document["progress"]["phase"] == "importing"


async def test_list_filters_validates_and_show_rejects_unknown_identities(tmp_path):
    service, _, _ = build(tmp_path, FakeRunner(tmp_path / "library"))
    await service.startup()
    document = (await terminal(service, [(await service.start(BOX_REF))["id"]]))[0]
    assert service.list(status="succeeded")["acquisitions"][0]["id"] == document["id"]
    assert service.list(status="failed")["acquisitions"] == []
    assert error_of(lambda: service.list(status="broken")).detail["code"] == "invalid_acquisition"
    assert error_of(lambda: service.list(limit=0)).detail["code"] == "invalid_acquisition"
    assert error_of(lambda: service.list(limit=51)).detail["code"] == "invalid_acquisition"
    assert error_of(lambda: service.show("acq-" + "0" * 32)).detail["code"] == "unknown_acquisition"
    assert error_of(lambda: service.show("../etc")).detail["code"] == "invalid_acquisition"


async def test_ineligible_unknown_and_path_or_url_inputs_are_rejected(tmp_path):
    service, _, _ = build(tmp_path, FakeRunner(tmp_path / "library"))
    await service.startup()
    ineligible = await catch(service.start(CART))
    assert ineligible.status == 409
    assert ineligible.detail["code"] == "acquisition_ineligible"
    assert ineligible.detail["reasons"] and ineligible.detail["estimate"]
    assert service.list()["acquisitions"] == []

    unknown = await catch(
        service.start(f"simready/absent_package@{REVISION}")
    )
    assert unknown.status == 404 and unknown.detail["code"] == "unknown_catalog_entry"

    for value in (
        "https://huggingface.co/datasets/nvidia/simready-assets",
        "C:\\assets\\box",
        "../outside",
        "simready/box_cardboard_a01",
        "",
    ):
        invalid = await catch(service.start(value))
        assert invalid.status == 422 and invalid.detail["code"] == "invalid_acquisition"


@pytest.mark.parametrize(
    "limits,reason",
    [
        (Limits(source_package_bytes=1), "Source package"),
        (Limits(adapted_package_bytes=1), "Adapted package"),
        (Limits(batch_bytes=1), "Batch source bytes"),
        (Limits(raw_cache_bytes=1), "Raw cache"),
    ],
)
async def test_budget_preflight_rejects_each_limit(tmp_path, limits, reason):
    service, _, _ = build(tmp_path, FakeRunner(tmp_path / "library"), limits=limits)
    await service.startup()
    exceeded = await catch(service.start(BOX_REF))
    assert exceeded.status == 409 and exceeded.detail["code"] == "budget_exceeded"
    assert exceeded.detail["estimate"]["withinLimits"] is False
    assert exceeded.detail["limits"]["batchBytes"] == limits.batch_bytes
    assert any(reason in item for item in exceeded.detail["reasons"])
    assert service.list()["acquisitions"] == []
    assert not (tmp_path / "state/acquisitions/budget.json").exists()


async def test_free_disk_reserve_is_checked_with_an_injected_probe(tmp_path):
    service, _, _ = build(
        tmp_path, FakeRunner(tmp_path / "library"), disk_usage=lambda: 1024
    )
    await service.startup()
    exceeded = await catch(service.start(BOX_REF))
    assert exceeded.detail["code"] == "budget_exceeded"
    assert any("Free disk" in item for item in exceeded.detail["reasons"])


async def test_success_settles_batch_charge_and_the_next_attempt_fits(tmp_path):
    runner = FakeRunner(tmp_path / "library")
    service, _, _ = build(tmp_path, runner, limits=Limits(batch_bytes=21530693 * 2 - 1))
    await service.startup()
    first = (await terminal(service, [(await service.start(BOX_REF))["id"]]))[0]
    assert first["status"] == "succeeded"
    ledger = json.loads((tmp_path / "state/acquisitions/budget.json").read_text(encoding="utf-8"))
    assert first["id"] not in ledger["charges"]
    assert ledger["reservations"] == {}
    second = await service.start(BOX_REF)
    assert second["status"] == "queued"
    assert (await terminal(service, [second["id"]]))[0]["status"] == "succeeded"


async def test_concurrency_is_two_running_and_one_queued(tmp_path):
    runner = GateRunner(tmp_path / "library")
    service, _, _ = build(tmp_path, runner, limits=Limits(concurrent_acquisitions=2))
    await service.startup()
    identifiers = [(await service.start(BOX_REF))["id"] for _ in range(3)]
    await asyncio.wait_for(runner.two_started.wait(), 10)
    statuses = [service.show(identifier)["status"] for identifier in identifiers]
    assert statuses.count("downloading") == 2
    assert statuses.count("queued") == 1
    runner.gate.set()
    documents = await terminal(service, identifiers)
    assert [item["status"] for item in documents] == ["succeeded"] * 3
    assert [item["result"]["deduplicated"] for item in documents].count(False) == 1


async def test_cancel_queued_and_running_and_terminal_is_idempotent(tmp_path):
    runner = GateRunner(tmp_path / "library")
    service, library, _ = build(tmp_path, runner, limits=Limits(concurrent_acquisitions=2))
    await service.startup()
    identifiers = [(await service.start(BOX_REF))["id"] for _ in range(3)]
    await asyncio.wait_for(runner.two_started.wait(), 10)
    queued = next(i for i in identifiers if service.show(i)["status"] == "queued")
    running = next(i for i in identifiers if service.show(i)["status"] == "downloading")
    cancelled = (await service.cancel(queued))["status"]
    assert cancelled == "cancelled"
    assert (await service.cancel(running))["cancelRequested"] is True
    runner.gate.set()
    documents = await terminal(service, identifiers)
    assert documents[0]["status"] == "cancelled"
    assert documents[1]["status"] == "succeeded"  # the other running record is untouched
    assert service.show(running)["status"] == "cancelled"
    assert service.show(queued)["status"] == "cancelled"
    assert (await service.cancel(queued))["status"] == "cancelled"
    assert library.resolve("environment/cardboard-box")  # a published sibling stays


async def test_retry_keeps_identity_usage_and_charges(tmp_path):
    runner = FakeRunner(
        tmp_path / "library",
        failures=[AcquisitionFailure("source_unavailable", "fixture network failure", True)],
    )
    service, _, _ = build(tmp_path, runner)
    await service.startup()
    started = await service.start(BOX_REF)
    failed = (await terminal(service, [started["id"]]))[0]
    assert failed["status"] == "failed" and failed["error"]["retryable"] is True
    assert failed["error"]["code"] == "source_unavailable"
    charges = json.loads(
        (tmp_path / "state/acquisitions/budget.json").read_text(encoding="utf-8")
    )["charges"]
    assert charges[failed["id"]]["sourceBytes"] == 21530693
    retried = await service.retry(failed["id"])
    assert retried["id"] == failed["id"] and retried["attempt"] == 2
    assert retried["status"] == "queued"
    assert retried["budget"]["reservationId"].endswith("@2")
    assert retried["budget"]["batchBytes"] == 21530693
    succeeded = (await terminal(service, [failed["id"]]))[0]
    assert succeeded["status"] == "succeeded" and succeeded["attempt"] == 2
    assert succeeded["result"]["assetVersion"] == "environment/cardboard-box@1"
    ledger = json.loads((tmp_path / "state/acquisitions/budget.json").read_text(encoding="utf-8"))
    assert failed["id"] not in ledger["charges"]
    assert ledger["reservations"] == {}
    invalid = await catch(service.retry(failed["id"]))
    assert invalid.detail["code"] == "invalid_acquisition"


async def test_restart_marks_active_work_interrupted_and_retry_is_explicit(tmp_path):
    runner = GateRunner(tmp_path / "library")
    service, library, _ = build(tmp_path, runner)
    await service.startup()
    started = await service.start(BOX_REF)
    deadline = time.monotonic() + 5
    while service.show(started["id"])["status"] != "downloading" and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await service.close()
    assert not (tmp_path / "library/assets/environment/cardboard-box").exists()

    restarted = AcquisitionService(
        tmp_path / "state",
        tmp_path / "library",
        library,
        service.catalog,
        runner=FakeRunner(tmp_path / "library"),
    )
    await restarted.startup()
    interrupted = restarted.show(started["id"])
    assert interrupted["status"] == "failed"
    assert interrupted["error"] == {
        "code": "interrupted",
        "message": interrupted["error"]["message"],
        "retryable": True,
    }
    assert not list((tmp_path / "state/acquisitions/cache").glob("*/*/*"))
    retried = await restarted.retry(started["id"])
    assert retried["attempt"] == 2
    succeeded = (await terminal(restarted, [started["id"]]))[0]
    assert succeeded["status"] == "succeeded"


async def test_cancel_after_the_atomic_import_keeps_the_published_version(tmp_path):
    service, library, _ = build(tmp_path, FakeRunner(tmp_path / "library"))
    await service.startup()
    original = library.import_staged
    entered, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return original(*args, **kwargs)

    library.import_staged = slow
    started = await service.start(BOX_REF)
    assert await asyncio.to_thread(entered.wait, 15)
    assert service.show(started["id"])["status"] == "importing"
    await service.cancel(started["id"])
    release.set()
    document = (await terminal(service, [started["id"]]))[0]
    assert document["status"] == "succeeded"
    assert document["result"]["assetVersion"] == "environment/cardboard-box@1"
    assert library.resolve("environment/cardboard-box@1")


def test_raw_cache_lru_keeps_active_entries_and_marks_complete(tmp_path):
    root = tmp_path / "cache"
    stamps = {"old": 1000, "new": 5000, "active": 500}
    for name, stamp in stamps.items():
        path = root / "simready" / REVISION / name
        path.mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"x" * 10)
        for entry in path.rglob("*"):
            entry.touch()
            os.utime(entry, (stamp, stamp))
    active = {("simready", REVISION, "active")}
    removed = cache.clean(root, active=active, limit=15, required=0)
    assert removed == 20
    assert (root / "simready" / REVISION / "active").is_dir()
    assert not (root / "simready" / REVISION / "old").exists()
    assert not (root / "simready" / REVISION / "new").exists()

    path = root / "simready" / REVISION / "active"
    assert cache.cached_bytes(root, "simready", REVISION, "active") == 0
    assert cache.mark_complete(root, "simready", REVISION, "active") is True
    assert cache.cached_bytes(root, "simready", REVISION, "active") == 10
    assert cache.complete(path)


def test_record_validation_rejects_tampered_documents(tmp_path):
    service, _, _ = build(tmp_path, FakeRunner(tmp_path / "library"))
    assert service.limits.full_document()["concurrentAcquisitions"] == 2
    with pytest.raises(ValueError):
        AcquisitionRecord.from_document({"schema": "lw.acquisition/1", "id": "acq-x"})


def test_subprocess_runner_job_document_uses_server_owned_paths(tmp_path):
    catalog = AssetCatalog(CATALOG_PATH)
    catalog.startup()
    entry = catalog.by_package["box_cardboard_a01"]
    record = AcquisitionRecord.create(
        entry.ref,
        source_document(entry),
        {
            "source": {"bytes": entry.source.bytes, "files": entry.source.files},
            "license": {},
            "limits": Limits().document(),
        },
    )
    runner = SubprocessRunner(tmp_path / "state", tmp_path / "library", catalog=catalog)
    job = runner.job_document(record)
    stage = tmp_path / "library/.acquisition-staging" / record.id
    assert job["schema"] == "lw.acquisition.worker-job/1"
    assert job["stagingDir"] == str(stage)
    assert job["cacheRoot"] == str(
        tmp_path / "state/acquisitions/cache/simready" / REVISION
    )
    assert job["provider"] == "simready"
    assert job["packageId"] == "box_cardboard_a01"
    assert job["recipe"] == "simready-newton-usdpreview-v1"
    assert job["limits"]["sourcePackageBytes"] == 512 * 1024 * 1024
    assert job["expected"]["bytes"] == 21530693
    assert job["expected"]["files"] == 36
    assert job["expected"]["localAssetId"] == "environment/cardboard-box"
    assert job["expected"]["license"]["name"] == "CC-BY-4.0"


def ledger_at(tmp_path, limits, *, free_bytes):
    root = tmp_path / "state/acquisitions"
    return BudgetLedger(
        root / "budget.json",
        limits,
        cache_root=root / "cache",
        disk_usage=lambda: free_bytes,
    )


def test_reserve_counts_other_reservations_in_the_raw_cache_budget(tmp_path):
    ledger = ledger_at(
        tmp_path,
        Limits(
            source_package_bytes=10_000,
            adapted_package_bytes=10_000,
            batch_bytes=100_000,
            raw_cache_bytes=1_000,
            free_disk_min_bytes=0,
        ),
        free_bytes=10**12,
    )
    first = ledger.reserve(
        record_id="acq-" + "a" * 32, attempt=1, source_bytes=600, adapted_upper=100
    )
    assert first["reservedDiskBytes"] == 700
    with pytest.raises(BudgetExceeded) as caught:
        ledger.reserve(
            record_id="acq-" + "b" * 32, attempt=1, source_bytes=600, adapted_upper=100
        )
    assert any("Raw cache" in reason and "reservations" in reason for reason in caught.value.reasons)
    # A failed reservation writes neither a charge nor a reservation.
    assert list(ledger.charges()) == ["acq-" + "a" * 32]
    assert list(ledger.reservations()) == ["acq-" + "a" * 32]


def test_preflight_counts_other_reservations_in_the_raw_cache_budget(tmp_path):
    catalog = AssetCatalog(CATALOG_PATH)
    catalog.startup()
    entry = catalog.by_package["box_cardboard_a01"]
    ledger = ledger_at(
        tmp_path,
        Limits(
            batch_bytes=100_000,
            raw_cache_bytes=entry.source.bytes + 1_000,
            free_disk_min_bytes=0,
        ),
        free_bytes=10**12,
    )
    ledger.reserve(
        record_id="acq-" + "c" * 32, attempt=1, source_bytes=2_000, adapted_upper=0
    )
    estimate, reasons = ledger.preflight(entry)
    assert estimate["withinLimits"] is False
    assert any("Raw cache" in reason and "reservations" in reason for reason in reasons)


def test_reserve_counts_other_reservations_in_the_free_disk_budget(tmp_path):
    ledger = ledger_at(
        tmp_path,
        Limits(
            source_package_bytes=10_000,
            adapted_package_bytes=10_000,
            batch_bytes=100_000,
            raw_cache_bytes=10**9,
            free_disk_min_bytes=500,
        ),
        free_bytes=1_000,
    )
    first = ledger.reserve(
        record_id="acq-" + "d" * 32, attempt=1, source_bytes=100, adapted_upper=200
    )
    assert first["reservedDiskBytes"] == 300
    with pytest.raises(BudgetExceeded) as caught:
        ledger.reserve(
            record_id="acq-" + "e" * 32, attempt=1, source_bytes=100, adapted_upper=200
        )
    assert any("Free disk" in reason and "reserved" in reason for reason in caught.value.reasons)


def test_retry_reservation_keeps_its_charge_without_double_counting(tmp_path):
    ledger = ledger_at(
        tmp_path,
        Limits(
            source_package_bytes=10_000,
            adapted_package_bytes=10_000,
            batch_bytes=1_000,
            raw_cache_bytes=10**9,
            free_disk_min_bytes=500,
        ),
        free_bytes=1_400,
    )
    identifier = "acq-" + "f" * 32
    first = ledger.reserve(
        record_id=identifier, attempt=1, source_bytes=600, adapted_upper=300
    )
    assert first["batchBytes"] == 600
    # A retry reacquires the same record's reservation before the old one is
    # counted twice; neither the batch charge nor the disk hold is duplicated.
    second = ledger.reserve(
        record_id=identifier, attempt=2, source_bytes=600, adapted_upper=300
    )
    assert second["reservationId"] == identifier + "@2"
    assert second["batchBytes"] == 600
    assert second["reservedDiskBytes"] == 900
    assert ledger.charges()[identifier] == {"sourceBytes": 600}
    assert list(ledger.reservations()) == [identifier]
    assert ledger.release(identifier)
    assert ledger.reservations() == {}
    assert ledger.charges()[identifier] == {"sourceBytes": 600}
    third = ledger.reserve(
        record_id=identifier, attempt=3, source_bytes=600, adapted_upper=300
    )
    assert third["batchBytes"] == 600


async def test_budget_error_names_other_active_reservations(tmp_path):
    limits = Limits(
        batch_bytes=100_000,
        raw_cache_bytes=21530693 + 1_000,
        free_disk_min_bytes=0,
    )
    service, _, _ = build(
        tmp_path, FakeRunner(tmp_path / "library"), limits=limits, disk_usage=lambda: 10**12
    )
    await service.startup()
    ledger = ledger_at(tmp_path, limits, free_bytes=10**12)
    ledger.reserve(
        record_id="acq-" + "9" * 32, attempt=1, source_bytes=2_000, adapted_upper=0
    )
    exceeded = await catch(service.start(BOX_REF))
    assert exceeded.status == 409 and exceeded.detail["code"] == "budget_exceeded"
    assert exceeded.detail["estimate"]["withinLimits"] is False
    assert exceeded.detail["limits"]["rawCacheBytes"] == limits.raw_cache_bytes
    assert any("Raw cache" in reason for reason in exceeded.detail["reasons"])
    assert service.list()["acquisitions"] == []


async def test_reserve_time_budget_failure_keeps_the_reasons(tmp_path, monkeypatch):
    limits = Limits(
        batch_bytes=100_000,
        raw_cache_bytes=21530693 + 1_000,
        free_disk_min_bytes=0,
    )
    service, _, _ = build(
        tmp_path, FakeRunner(tmp_path / "library"), limits=limits, disk_usage=lambda: 10**12
    )
    await service.startup()
    ledger = ledger_at(tmp_path, limits, free_bytes=10**12)
    ledger.reserve(
        record_id="acq-" + "8" * 32, attempt=1, source_bytes=2_000, adapted_upper=0
    )
    original = service._budget.preflight

    def optimistic(entry, **kwargs):
        estimate, _ = original(entry, **kwargs)
        return estimate, ()

    # Simulate losing the race between preflight and the locked reservation.
    monkeypatch.setattr(service._budget, "preflight", optimistic)
    exceeded = await catch(service.start(BOX_REF))
    assert exceeded.status == 409 and exceeded.detail["code"] == "budget_exceeded"
    assert exceeded.detail["limits"]["rawCacheBytes"] == limits.raw_cache_bytes
    assert any(
        "Raw cache" in reason and "reservations" in reason
        for reason in exceeded.detail["reasons"]
    )
    assert service.list()["acquisitions"] == []



async def test_success_releases_batch_charge_but_failure_keeps_it_for_retry(tmp_path):
    source = 21_530_693
    limits = Limits(batch_bytes=source + 1)
    runner = FakeRunner(tmp_path / "library")
    service, _, _ = build(tmp_path, runner, limits=limits)
    await service.startup()
    first = await service.start(BOX_REF)
    assert (await terminal(service, [first["id"]]))[0]["status"] == "succeeded"
    # A successful acquisition settles its charge, so the next one fits.
    second = await service.start(BOX_REF)
    assert (await terminal(service, [second["id"]]))[0]["status"] == "succeeded"

    failing = FakeRunner(
        tmp_path / "failed" / "library",
        failures=[AcquisitionFailure("source_unavailable", "temporary", retryable=True)],
    )
    failed_service, _, _ = build(tmp_path / "failed", failing, limits=limits)
    await failed_service.startup()
    failed = await failed_service.start(BOX_REF)
    assert (await terminal(failed_service, [failed["id"]]))[0]["status"] == "failed"
    assert failed["id"] in failed_service._budget.charges()
    retried = await failed_service.retry(failed["id"])
    assert (await terminal(failed_service, [retried["id"]]))[0]["status"] == "succeeded"
    assert failed["id"] not in failed_service._budget.charges()


async def test_non_retryable_failure_cannot_be_retried(tmp_path):
    runner = FakeRunner(
        tmp_path / "library",
        failures=[AcquisitionFailure("adaptation_gap", "unsupported UDIM", retryable=False)],
    )
    service, _, _ = build(tmp_path, runner)
    await service.startup()
    started = await service.start(BOX_REF)
    record = (await terminal(service, [started["id"]]))[0]
    assert record["status"] == "failed" and record["error"]["retryable"] is False
    refused = await catch(service.retry(record["id"]))
    assert refused.status == 409 and refused.detail["code"] == "acquisition_not_retryable"
    assert runner.runs == 1
    assert record["id"] not in service._budget.charges()
    assert record["id"] not in service._budget.reservations()
