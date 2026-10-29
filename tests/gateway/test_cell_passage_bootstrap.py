"""Owned demo API identity uses the real gateway lifecycle, without a physics Source."""

import socket

import pytest
from fastapi.testclient import TestClient

from tools.cell_passage import _available, gateway_app


def test_owned_port_probe_rejects_listener_but_accepts_closed_server_connections():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(OSError):
            _available(port)
        with socket.create_connection(("127.0.0.1", port)) as peer:
            accepted, _ = listener.accept()
            accepted.close()
            assert peer.recv(1) == b""
    _available(port)


def test_bootstrap_gateway_proves_its_own_process_identity_before_client_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("SPATIAL_PASSAGE_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("SPATIAL_PASSAGE_OWNER_NONCE", "owned-fixture-nonce")
    monkeypatch.setenv("SPATIAL_ALLOWED_ORIGINS", "http://127.0.0.1:5223")
    with TestClient(gateway_app()) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.headers["X-Passage-Owner"] == "owned-fixture-nonce"
        assert response.json()["sessions"] == []
