"""Owned demo API identity uses the real gateway lifecycle, without a physics Source."""

from fastapi.testclient import TestClient

from tools.cell_passage import gateway_app


def test_bootstrap_gateway_proves_its_own_process_identity_before_client_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("SPATIAL_PASSAGE_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("SPATIAL_PASSAGE_OWNER_NONCE", "owned-fixture-nonce")
    monkeypatch.setenv("SPATIAL_ALLOWED_ORIGINS", "http://127.0.0.1:5223")
    with TestClient(gateway_app()) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.headers["X-Passage-Owner"] == "owned-fixture-nonce"
        assert response.json()["sessions"] == []
