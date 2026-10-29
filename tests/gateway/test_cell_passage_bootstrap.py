"""Owned demo API identity uses the real gateway lifecycle, without a physics Source."""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tools.cell_passage import _available, gateway_app

ROOT_VARIABLES = ("LW_LIBRARY", "SPATIAL_STATE_ROOT", "SPATIAL_RECORDINGS_ROOT",
                  "SPATIAL_SCENE_PREVIEWS_ROOT", "SPATIAL_CATALOG_METADATA_ROOT")


@pytest.fixture(autouse=True)
def preserve_factory_environment():
    previous = {name: os.environ.get(name) for name in ROOT_VARIABLES}
    yield
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


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
    monkeypatch.setenv("SPATIAL_CATALOG_METADATA_ROOT", str(tmp_path / "unowned-inherited-root"))
    with TestClient(gateway_app()) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.headers["X-Passage-Owner"] == "owned-fixture-nonce"
        assert response.json()["sessions"] == []


def test_bootstrap_overrides_foreign_metadata_root_without_touching_it(tmp_path, monkeypatch):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "sentinel.bin"
    sentinel.write_bytes(b"foreign catalog bytes must not change")
    run = tmp_path / "owned-run"
    monkeypatch.setenv("SPATIAL_PASSAGE_RUN_ROOT", str(run))
    monkeypatch.setenv("SPATIAL_PASSAGE_OWNER_NONCE", "metadata-owner")
    monkeypatch.setenv("SPATIAL_ALLOWED_ORIGINS", "http://127.0.0.1:5223")
    monkeypatch.setenv("SPATIAL_CATALOG_METADATA_ROOT", str(foreign))
    monkeypatch.setenv("LW_LIBRARY", str(run / "runtime/library"))
    with TestClient(gateway_app()) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.app.state.metadata_store.root == run / "runtime/catalog-metadata"
        assert (run / "runtime/catalog-metadata").is_dir()
        if os.name != "nt":
            assert (run / "runtime/catalog-metadata").stat().st_mode & 0o777 == 0o700
    assert list(foreign.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"foreign catalog bytes must not change"


def test_owned_gateway_factory_transitive_imports_do_not_load_physics_engines(tmp_path):
    foreign = {}
    for name in ROOT_VARIABLES:
        path = tmp_path / name
        path.mkdir()
        (path / "sentinel").write_bytes(b"cold-import sentinel")
        foreign[name] = str(path)
    owned = tmp_path / "owned"
    env = os.environ | {"SPATIAL_PASSAGE_RUN_ROOT": str(owned),
                        "SPATIAL_PASSAGE_OWNER_NONCE": "engine-free",
                        "SPATIAL_ALLOWED_ORIGINS": "http://127.0.0.1:5223",
                        **foreign}
    code = """
import os, sys
from pathlib import Path
names = ('LW_LIBRARY', 'SPATIAL_STATE_ROOT', 'SPATIAL_RECORDINGS_ROOT',
         'SPATIAL_SCENE_PREVIEWS_ROOT', 'SPATIAL_CATALOG_METADATA_ROOT')
foreign = [Path(os.environ[name]) for name in names]
from tools.cell_passage import gateway_app
app = gateway_app()
for path in foreign:
    assert [child.name for child in path.iterdir()] == ['sentinel']
    assert (path / 'sentinel').read_bytes() == b'cold-import sentinel'
owned = Path(os.environ['SPATIAL_PASSAGE_RUN_ROOT']) / 'runtime/catalog-metadata'
assert app.state.metadata_store.root == owned
for name, directory in zip(names, ('library', 'state', 'recordings', 'previews', 'catalog-metadata')):
    path = Path(os.environ['SPATIAL_PASSAGE_RUN_ROOT']) / 'runtime' / directory
    assert Path(os.environ[name]) == path and path.is_dir()
    if os.name != 'nt':
        assert path.stat().st_mode & 0o777 == 0o700
blocked = [name for name in sys.modules
           if name.split('.')[0] in ('factory', 'newton', 'warp')]
assert not blocked, blocked
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("missing", ["SPATIAL_PASSAGE_OWNER_NONCE", "SPATIAL_ALLOWED_ORIGINS"])
def test_missing_factory_identity_inputs_fail_before_importing_default_app(tmp_path, missing):
    env = os.environ | {"SPATIAL_PASSAGE_RUN_ROOT": str(tmp_path),
                        "SPATIAL_PASSAGE_OWNER_NONCE": "required-owner",
                        "SPATIAL_ALLOWED_ORIGINS": "http://127.0.0.1:5223"}
    env.pop(missing)
    code = """
import sys
from tools.cell_passage import gateway_app
try:
    gateway_app()
except KeyError:
    pass
else:
    raise AssertionError('Required factory input was not rejected')
assert 'apps.api.app.main' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
