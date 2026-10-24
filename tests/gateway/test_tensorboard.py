"""Public launch reuses its owner and cleans up; fixture is explicitly not real TensorBoard."""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings


def test_runtime_unavailable_does_not_import_tensorboard_or_create_process():
    app = create_app(Settings())
    with TestClient(app) as client:
        response = client.post("/api/v1/tensorboard")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "tensorboard_runtime_unavailable"
        assert app.state.tensorboard.process is None


def test_concurrent_owned_fake_launch_reuse_health_responsiveness_and_shutdown():
    app = create_app(Settings())
    service = app.state.tensorboard
    fixture = Path(__file__).parents[1] / "fixtures/fake_tensorboard.py"
    service.command = lambda port, prefix: [sys.executable, str(fixture), str(port), prefix]
    with TestClient(app) as client, ThreadPoolExecutor(3) as pool:
        first = pool.submit(client.post, "/api/v1/tensorboard")
        second = pool.submit(client.post, "/api/v1/tensorboard")
        assert pool.submit(client.get, "/api/v1/health").result(timeout=2).status_code == 200
        one, two = first.result(timeout=10), second.result(timeout=10)
        assert one.status_code == two.status_code == 200, (one.text, two.text)
        assert one.json() == two.json()
        assert one.json()["url"].startswith("http://127.0.0.1:")
        assert "/lw-tensorboard-" in one.json()["url"]
        owner = service.process
        assert client.post("/api/v1/tensorboard").json() == one.json()
        assert service.process is owner and len(list(service.root.glob("process-*.json"))) == 1
    assert owner.process.poll() is not None and owner.cleanup.is_file()


def test_remote_viewer_cannot_launch_tensorboard():
    settings = Settings(remote=True, operator_token="operator", viewer_token="reader")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/v1/tensorboard", headers={"Authorization": "Bearer reader"})
        assert response.status_code == 403 and app.state.tensorboard.process is None
