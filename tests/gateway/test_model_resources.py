"""Public resources API: durable configuration, operator writes and credential isolation."""

import json

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system
from apps.api.app.settings import Settings
from packages.vault import Vault

SENTINEL = "FAKE_MODEL_VAULT_SENTINEL_NEVER_A_REAL_KEY_42"
MODEL = {
    "provider": "fake-provider",
    "modelId": "fake-model",
    "defaultThinking": "high",
    "credentialName": "llm/fake",
}


@pytest.fixture(autouse=True)
def resource_gateway_has_no_source_or_host_probes(monkeypatch):
    async def no_source(*args, **kwargs):
        raise AssertionError("Resource metadata must not create a spatial Source")

    def fake_sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Fake resource test sampler"))

    monkeypatch.setattr("apps.api.app.platform_monitor.HostSampler.sample", fake_sample)
    monkeypatch.setattr("apps.api.app.session_manager.SessionManager.create", no_source)
    monkeypatch.delenv("SPATIAL_LIVE_BINDINGS", raising=False)


@pytest.fixture
def configured(tmp_path):
    settings = Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings")
    Vault(settings.state_root).set("llm/fake", SENTINEL)
    return settings


def test_model_registration_filter_default_unknown_metrics_and_restart(configured):
    with TestClient(create_app(configured)) as http:
        assert http.get("/api/v1/models").json() == {"models": [], "defaultRef": None, "canManage": True}
        response = http.post("/api/v1/models", json=MODEL)
        assert response.status_code == 201
        model = response.json()
        assert model == MODEL | {
            "ref": "fake-provider/fake-model",
            "isDefault": False,
            "health": "unknown",
            "latencyP50Ms": None,
            "users": None,
            "healthReason": "Adapter request metrics are not connected",
        }
        assert http.post("/api/v1/models", json=MODEL).status_code == 409
        assert http.get("/api/v1/models?q=absent").json()["models"] == []
        assert http.get("/api/v1/models?q=FAKE-PROVIDER").json()["models"] == [model]
        assert http.put("/api/v1/models/default", json={"ref": "absent/model"}).status_code == 404
        assert http.put("/api/v1/models/default", json={"ref": model["ref"]}).json() == {
            "defaultRef": model["ref"]
        }
    with TestClient(create_app(configured)) as restarted:
        restored = restarted.get("/api/v1/models").json()
        assert restored["models"] == [model | {"isDefault": True}]
        assert restored["defaultRef"] == model["ref"]


def test_human_prices_keep_unknown_distinct_from_zero_audit_and_reject_stale_edit(configured):
    with TestClient(create_app(configured)) as http:
        initial = http.get("/api/v1/prices").json()
        assert initial == {
            "version": 1,
            "revision": 0,
            "models": [],
            "gpus": [],
            "audit": [],
            "canEdit": True,
        }
        incoming = {
            "revision": 0,
            "models": [
                {
                    "provider": "fake-provider",
                    "modelId": "fake-model",
                    "inputUsdPerMillion": 0,
                    "outputUsdPerMillion": 2.75,
                }
            ],
            "gpus": [{"nodeId": "fake-node", "usdPerHour": 0.25}],
        }
        updated = http.put("/api/v1/prices", json=incoming, headers={"X-Actor": "user:price-editor"})
        assert updated.status_code == 200
        saved = updated.json()
        assert saved["revision"] == 1
        assert saved["models"][0]["inputUsdPerMillion"] == 0
        assert saved["models"][0]["cacheReadUsdPerMillion"] is None
        assert saved["gpus"] == incoming["gpus"]
        assert saved["audit"][0]["actor"] == {"kind": "user", "name": "price-editor"}
        assert saved["audit"][0]["revision"] == 1
        assert saved["audit"][0]["at"]
        assert http.put("/api/v1/prices", json=incoming).status_code == 409
        invalid = incoming | {"revision": 1, "gpus": [{"nodeId": "fake-node", "usdPerHour": -1}]}
        assert http.put("/api/v1/prices", json=invalid).status_code == 422
        assert http.get("/api/v1/prices").json() == saved
    with TestClient(create_app(configured)) as restarted:
        assert restarted.get("/api/v1/prices").json() == saved
    assert (configured.state_root / "prices.yaml").is_file()


def test_vault_names_only_no_write_route_no_validation_or_log_leak(configured, caplog):
    with TestClient(create_app(configured)) as http:
        documents = [http.get("/api/v1/vault")]
        assert documents[0].json() == {"names": ["llm/fake"]}
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = http.request(
                method, "/api/v1/vault", json={"name": "llm/forbidden", "value": SENTINEL}
            )
            assert response.status_code == 405
            documents.append(response)
        for body in (
            MODEL | {"credentialValue": SENTINEL},
            MODEL | {"modelId": SENTINEL + " invalid"},
            MODEL | {SENTINEL: "accidental field name"},
        ):
            response = http.post("/api/v1/models", json=body)
            assert response.status_code == 422
            documents.append(response)
        response = http.post("/api/v1/models", json=MODEL | {"credentialName": "llm/missing"})
        assert response.status_code == 404
        documents.append(response)
        assert http.post("/api/v1/models", json=MODEL).status_code == 201
        documents += [http.get(path) for path in ("/api/v1/models", "/api/v1/prices", "/api/v1/health")]
        assert all(SENTINEL not in response.text for response in documents)
        assert SENTINEL not in caplog.text
        assert SENTINEL not in json.dumps(http.get("/openapi.json").json())
    for path in configured.recordings_root.rglob("*"):
        if path.is_file():
            assert SENTINEL.encode() not in path.read_bytes()


def test_viewers_can_read_names_but_cannot_change_models_or_prices(configured):
    remote = Settings(
        state_root=configured.state_root,
        recordings_root=configured.recordings_root,
        remote=True,
        operator_token="fake-operator",
        viewer_token="fake-viewer",
    )
    with TestClient(create_app(remote), headers={"Authorization": "Bearer fake-viewer"}) as http:
        assert http.get("/api/v1/vault").json() == {"names": ["llm/fake"]}
        assert http.get("/api/v1/models").json()["canManage"] is False
        assert http.get("/api/v1/prices").json()["canEdit"] is False
        for method, path, body in (
            ("POST", "/api/v1/models", MODEL),
            ("PUT", "/api/v1/models/default", {"ref": "fake-provider/fake-model"}),
            ("PUT", "/api/v1/prices", {"revision": 0}),
        ):
            assert http.request(method, path, json=body).status_code == 403


def test_corrupt_resource_file_reports_safe_error_without_overwriting(configured):
    path = configured.state_root / "models.yaml"
    path.write_text("models: [invalid-sensitive-placeholder", encoding="utf-8")
    with TestClient(create_app(configured)) as http:
        response = http.get("/api/v1/models")
        assert response.status_code == 503
        assert "invalid-sensitive-placeholder" not in response.text
        assert http.post("/api/v1/models", json=MODEL).status_code == 503
    assert path.read_text(encoding="utf-8") == "models: [invalid-sensitive-placeholder"
