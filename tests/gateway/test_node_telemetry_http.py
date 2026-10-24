from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings


def test_authenticated_heartbeats_expose_real_history_gaps_expiry_and_restart():
    app = create_app(Settings(node_token="telemetry-test-token"))
    clock = [100.0]
    app.state.jobs.clock = lambda: clock[0]
    headers = {"X-Node-Token": "telemetry-test-token", "X-Node-Instance": "first"}
    body = {"instanceId": "first", "hardware": "GPU-free telemetry fixture", "gpus": [], "reports": []}
    with TestClient(app) as client:
        assert client.get("/api/v1/nodes").json()["nodes"] == []
        for at, utilization, reason in ((100.0, 0.0, None), (120.0, 70.0, None),
                                       (140.0, None, "Driver did not report utilization")):
            clock[0] = at
            body["gpus"] = [{"id": "GPU-test", "name": "No physical GPU", "observedAt": at,
                             "utilization": utilization, "memoryUsedMb": None, "unavailableReason": reason}]
            assert client.post("/api/v1/nodes/local/heartbeat", json=body).status_code == 403
            response = client.post("/api/v1/nodes/local/heartbeat", json=body, headers=headers)
            assert response.status_code == 200, response.text
        node = client.get("/api/v1/nodes").json()["nodes"][0]
        assert node["state"] == "online" and not node["leases"] and not node["queue"]
        gpu = node["gpus"][0]
        assert gpu["utilization"] is None
        samples = gpu["history"]["samples"]
        assert [sample["at"] for sample in samples] == [100.0, 120.0, 140.0]
        assert [sample["utilization"] for sample in samples] == [0.0, 70.0, None]
        assert samples[-1]["unavailableReason"] == "Driver did not report utilization"
        clock[0] = 4000.0
        node = client.get("/api/v1/nodes").json()["nodes"][0]
        assert node["state"] == "offline" and node["gpus"][0]["history"]["samples"] == []
        body["instanceId"] = "second"
        body["gpus"][0].update(observedAt=4000.0, utilization=20.0, unavailableReason=None)
        headers["X-Node-Instance"] = "second"
        assert client.post("/api/v1/nodes/local/heartbeat", json=body, headers=headers).status_code == 200
        node = client.get("/api/v1/nodes").json()["nodes"][0]
        assert node["state"] == "online"
        assert [sample["utilization"] for sample in node["gpus"][0]["history"]["samples"]] == [20.0]
