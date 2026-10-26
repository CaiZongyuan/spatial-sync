"""Public maintenance controls, exact receipts and durable recovery."""

import copy

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.catalog_metadata import MetadataStore

BASE = "/api/v1/library/catalog/maintenance"
REVISION = "a" * 40


def gateway(tmp_path, **options):
    store = MetadataStore(tmp_path / "metadata")
    app = create_app(metadata_store=store, metadata_launch_workers=False, **options)
    return app, store


def staged(client, *, budgets=None, count=2):
    enabled = client.patch(BASE + "/collections/simready", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    started = client.post(
        BASE + "/runs",
        json={
            "collectionId": "simready",
            "revision": REVISION,
            "budgets": budgets,
        },
    )
    assert started.status_code == 202, started.text
    identifier = started.json()["id"]
    entries = [
        {
            "upstreamId": "beaker_" + str(index),
            "facts": {"sourceBytes": None},
            "preview": {"state": "missing", "path": None, "role": None, "sha256": None},
        }
        for index in range(count)
    ]
    response = client.post(
        BASE + "/runs/" + identifier + "/inventory", json={"entries": entries, "offset": 0}
    )
    assert response.status_code == 200, response.text
    complete = client.post(
        BASE + "/runs/" + identifier + "/inventory/complete",
        json={
            "revision": REVISION,
            "complete": True,
            "counts": {"entries": count, "expectedEntries": count},
            "usage": {"requests": 3, "networkBytes": 1200, "previewBytes": 0},
        },
    )
    assert complete.status_code == 200, complete.text
    return complete.json()


def claimed(client, identifier):
    response = client.post(BASE + "/runs/" + identifier + "/claim", json={"limit": 24})
    assert response.status_code == 200, response.text
    return response.json()


def receipt(batch):
    return {
        "inputKey": batch["inputKey"],
        "nativeSessionId": "deterministic-test-session",
        "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
        "results": [
            {
                "upstreamId": entry["upstreamId"],
                "inputDigest": entry["inputDigest"],
                "fields": {
                    "name": {"en": "Glass beaker", "zh": "玻璃烧杯"},
                    "category": "container",
                    "tags": ["beaker", "烧杯"],
                },
            }
            for entry in batch["entries"]
        ],
    }


def test_public_batch_idempotence_exact_usage_and_completed_retry(tmp_path):
    app, store = gateway(tmp_path)
    with TestClient(app) as client:
        run = staged(client)
        batch = claimed(client, run["id"])
        result = receipt(batch)
        path = BASE + "/runs/" + run["id"] + "/batches/" + batch["id"] + "/submit"
        submitted = client.post(path, json=result)
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["usage"]["modelTokens"] == 10
        assert client.post(path, json=result).json() == submitted.json()
        changed = copy.deepcopy(result)
        changed["results"][0]["fields"]["name"]["zh"] = "错误替换"
        assert client.post(path, json=changed).status_code == 409
        finish = client.post(BASE + "/runs/" + run["id"] + "/finish")
        assert finish.status_code == 200, finish.text
        assert finish.json()["status"] == "succeeded"
        assert finish.json()["coverage"]["organized"] == 2
        assert finish.json()["usage"]["modelCalls"] == 1
        assert client.post(path, json=result).json() == submitted.json()
        assert all(entry["name"]["zh"] == "玻璃烧杯" for entry in store.entries())
        history = store.organization_history(
            "simready", batch["entries"][0]["upstreamId"], batch["entries"][0]["inputDigest"]
        )
        assert len(history) == 1


def test_invalid_usage_never_commits_organization_and_unknown_usage_stops_calls(tmp_path):
    app, store = gateway(tmp_path)
    with TestClient(app) as client:
        run = staged(client)
        batch = claimed(client, run["id"])
        result = receipt(batch)
        path = BASE + "/runs/" + run["id"] + "/batches/" + batch["id"] + "/submit"
        invalid = copy.deepcopy(result)
        invalid["usage"]["totalTokens"] = -1
        assert client.post(path, json=invalid).status_code == 400
        assert all(
            entry["organization"]["version"] == 0 for entry in store.inventory_entries(run["inventoryId"])
        )
        result["usage"] = {"totalTokens": None}
        submitted = client.post(path, json=result)
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["usage"]["modelTokens"] is None
        assert submitted.json()["budgetExhausted"]
        assert claimed(client, run["id"])["entries"] == []


def test_gateway_restart_replays_original_receipt_after_partial_core_commit(tmp_path, monkeypatch):
    app, store = gateway(tmp_path)
    original = store.submit_organization
    crashed = False

    def crash_once(*args, **kwargs):
        nonlocal crashed
        value = original(*args, **kwargs)
        if not crashed:
            crashed = True
            raise RuntimeError("Injected host crash after a committed organization prefix")
        return value

    monkeypatch.setattr(store, "submit_organization", crash_once)
    with TestClient(app, raise_server_exceptions=False) as client:
        run = staged(client)
        batch = claimed(client, run["id"])
        result = receipt(batch)
        path = BASE + "/runs/" + run["id"] + "/batches/" + batch["id"] + "/submit"
        assert client.post(path, json=result).status_code == 500
        assert (
            sum(
                entry["organization"]["version"] == 1 for entry in store.inventory_entries(run["inventoryId"])
            )
            == 1
        )
    restarted, reopened = gateway(tmp_path)
    with TestClient(restarted) as client:
        recovered = client.get(BASE + "/runs/" + run["id"]).json()
        assert recovered["status"] == "interrupted"
        assert recovered["usage"]["modelCalls"] == 1
        assert recovered["usage"]["modelTokens"] == 10
        assert recovered["coverage"]["organized"] == 2
        resumed = client.post(BASE + "/runs/" + run["id"] + "/resume")
        assert resumed.status_code == 202, resumed.text
        assert claimed(client, run["id"])["entries"] == []
        finished = client.post(BASE + "/runs/" + run["id"] + "/finish")
        assert finished.status_code == 200, finished.text
        assert finished.json()["usage"]["modelCalls"] == 1
        assert all(entry["organization"]["version"] == 1 for entry in reopened.entries())
        assert client.post(path, json=result).json()["usage"]["modelTokens"] == 10


def test_unconfigured_runtime_publishes_inventory_with_honest_pending_coverage(tmp_path):
    app, store = gateway(tmp_path)
    with TestClient(app) as client:
        run = staged(client)
        finished = client.post(BASE + "/runs/" + run["id"] + "/finish")
        assert finished.status_code == 200, finished.text
        value = finished.json()
        assert value["status"] == "partial" and not value["runtime"]["configured"]
        assert value["usage"]["modelCalls"] == 0 and value["usage"]["modelTokens"] is None
        assert value["coverage"]["indexed"] == 2 and value["coverage"]["organized"] == 0
        assert store.latest_release()["entryCount"] == 2
        assert client.get(BASE).json()["release"]["id"] == value["releaseId"]


def test_zero_model_budget_prevents_claim_and_preserves_old_release(tmp_path):
    profile = {"name": "fixture", "model": "deterministic-stand-in", "command": ["unused"]}
    app, store = gateway(tmp_path, metadata_agent_profile=profile)
    with TestClient(app) as client:
        run = staged(client, budgets={"modelTokens": 0})
        assert claimed(client, run["id"])["entries"] == []
        value = client.get(BASE + "/runs/" + run["id"]).json()
        assert value["budgetExhausted"] and value["usage"]["modelCalls"] == 0
        finished = client.post(BASE + "/runs/" + run["id"] + "/finish")
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "budget_exhausted"
        assert store.latest_release()["entryCount"] == 2


def test_worker_grant_is_scoped_and_revoked_on_cancel_even_in_local_mode(tmp_path, monkeypatch):
    app, _ = gateway(tmp_path)
    captured = {}

    def retain_token(record, token, callback_url):
        captured[record["id"]] = token

    monkeypatch.setattr(app.state.metadata_maintenance, "_launch", retain_token)
    with TestClient(app) as client:
        run = staged(client)
        grant = {"Authorization": "Bearer " + captured[run["id"]]}
        permitted = client.post(
            BASE + "/runs/" + run["id"] + "/phase", json={"phase": "organizing"}, headers=grant
        )
        assert permitted.status_code == 200
        assert (
            client.patch(BASE + "/collections/simready", json={"enabled": False}, headers=grant).status_code
            == 403
        )
        assert client.post(BASE + "/runs/meta-other/claim", json={}, headers=grant).status_code == 403
        assert client.post(BASE + "/runs/" + run["id"] + "/cancel").status_code == 200
        assert (
            client.post(
                BASE + "/runs/" + run["id"] + "/phase", json={"phase": "organizing"}, headers=grant
            ).status_code
            == 403
        )


def test_correction_conflict_is_item_scoped_and_manual_fields_survive_retry(tmp_path):
    app, store = gateway(tmp_path)
    with TestClient(app) as client:
        run = staged(client)
        batch = claimed(client, run["id"])
        selected = batch["entries"][0]
        corrected = client.post(
            BASE + "/corrections",
            json={
                "collectionId": "simready",
                "upstreamId": selected["upstreamId"],
                "baseVersion": 0,
                "fields": {"name": {"zh": "人工名称"}},
                "reason": "Publisher terminology clarified",
            },
        )
        assert corrected.status_code == 200, corrected.text
        path = BASE + "/runs/" + run["id"] + "/batches/" + batch["id"] + "/submit"
        submitted = client.post(path, json=receipt(batch))
        assert submitted.status_code == 200
        assert [item["status"] for item in submitted.json()["outcomes"]] == ["failed", "succeeded"]
        assert submitted.json()["outcomes"][0]["error"] == "correction_conflict"
        finished = client.post(BASE + "/runs/" + run["id"] + "/finish")
        assert finished.json()["status"] == "partial"
        resumed = client.post(BASE + "/runs/" + run["id"] + "/resume")
        assert resumed.status_code == 202
        repair = claimed(client, run["id"])
        assert len(repair["entries"]) == 1 and repair["entries"][0]["correctionVersion"] == 1
        fixed = client.post(
            BASE + "/runs/" + run["id"] + "/batches/" + repair["id"] + "/submit", json=receipt(repair)
        )
        assert fixed.status_code == 200
        assert client.post(BASE + "/runs/" + run["id"] + "/finish").json()["status"] == "succeeded"
        selected_entry = next(
            item for item in store.entries() if item["upstreamId"] == selected["upstreamId"]
        )
        assert selected_entry["name"]["zh"] == "人工名称"


def test_actual_consumer_index_failure_keeps_published_view_and_retry_works(tmp_path, monkeypatch):
    from packages.catalog import maintained

    app, store = gateway(tmp_path)
    original = maintained.build_semantic_index
    with TestClient(app, raise_server_exceptions=False) as client:
        before = client.get("/api/v1/library/search", params={"scope": "online"}).json()
        run = staged(client)

        def broken(*args, **kwargs):
            raise RuntimeError("Injected actual consuming index failure")

        monkeypatch.setattr(maintained, "build_semantic_index", broken)
        failed = client.post(BASE + "/runs/" + run["id"] + "/finish")
        assert failed.status_code == 500
        assert store.latest_release() is None
        retained = client.get("/api/v1/library/search", params={"scope": "online"}).json()
        before["snapshot"].pop("ageSeconds")
        retained["snapshot"].pop("ageSeconds")
        assert retained == before
        monkeypatch.setattr(maintained, "build_semantic_index", original)
        finished = client.post(BASE + "/runs/" + run["id"] + "/finish")
        assert finished.status_code == 200, finished.text
        assert store.latest_release()["id"] == finished.json()["releaseId"]
        after = client.get("/api/v1/library/search", params={"scope": "online"}).json()
        assert after["snapshot"]["id"] != before["snapshot"]["id"]
        assert after["counts"]["matching"] == 2


def test_evidence_summary_bounds_binary_and_restart_reservation_usage(tmp_path):
    app, store = gateway(tmp_path)
    digest = store.put_blob(b"source " * 10000)["sha256"]
    binary = store.put_blob(b"\xff\x00" * 1000)["sha256"]
    with TestClient(app) as client:
        summary = client.get(BASE + "/evidence/" + digest, params={"limit": 128}).json()
        assert summary["truncated"] and summary["nextOffset"] == 128
        assert len(summary["text"].encode()) == 128
        assert client.get(BASE + "/evidence/" + binary).json()["encoding"] == "binary"
        assert client.get(BASE + "/evidence/" + digest, params={"view": "raw"}).content == b"source " * 10000
        run = staged(client)
        batch = claimed(client, run["id"])
        assert (
            client.post(BASE + "/runs/" + run["id"] + "/batches/" + batch["id"] + "/execution").status_code
            == 200
        )
    restarted, _ = gateway(tmp_path)
    with TestClient(restarted) as client:
        interrupted = client.get(BASE + "/runs/" + run["id"]).json()
        assert interrupted["usage"]["modelCalls"] is None and interrupted["usage"]["modelTokens"] is None
        assert client.post(BASE + "/runs/" + run["id"] + "/resume").status_code == 409
