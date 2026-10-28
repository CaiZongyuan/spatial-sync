"""Real gateway action/recording path with an explicit external actuator fixture.

This proves API semantics, not Newton geometry or successful physical pipetting.
"""

import json

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.log import read_segment
from tests.fixtures.cell_passage import build_workcell_host


def test_paused_control_commit_is_visible_through_http_and_recorded_without_protocol_changes(tmp_path):
    class WorkcellSource(SyntheticSource):
        async def start(self, scene_id, options):
            manifest, _ = await super().start(scene_id, options)
            self.fixed_poses = super()._poses()
            self.host, self.actuator = build_workcell_host(tmp_path)
            self.catalog = self.host.catalog
            self.catalog.sceneRevision = manifest.sceneRevision
            return manifest, self.catalog

        def _poses(self):
            return self.fixed_poses if hasattr(self, "fixed_poses") else super()._poses()

    with TestClient(create_app(source_factory=lambda kind, sid: WorkcellSource(sid))) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        route = f"/api/v1/sessions/{session['sessionId']}"
        pause = client.post(route + "/commands", json={"requestId": "pause", "command": "pause"})
        assert pause.status_code == 200, pause.text
        params = {
            "owner": "auto",
            "epoch": 0,
            "operationId": "handoff",
            "nextOwner": "human",
            "mode": "manual",
        }
        accepted = client.post(
            route + "/actions",
            json={
                "requestId": "handoff",
                "entityKey": "/workcell",
                "action": "requestControl",
                "params": params,
            },
        )
        assert accepted.status_code == 202, accepted.text
        values = client.get(route + "/state", params={"keys": "/workcell"}).json()["entities"]["/workcell"][
            "values"
        ]
        assert (values["controlMode"], values["controlEpoch"]) == ("holding", 1)
        stale = client.post(
            route + "/actions",
            json={
                "requestId": "late",
                "entityKey": "/workcell",
                "action": "move",
                "params": {"owner": "auto", "epoch": 0, "operationId": "late", "position": [0, 0, 0]},
            },
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_control"
        replay = client.post(
            route + "/actions",
            json={
                "requestId": "handoff",
                "entityKey": "/workcell",
                "action": "requestControl",
                "params": params,
            },
        )
        assert replay.json()["invocationId"] == accepted.json()["invocationId"]
        resumed = client.post(route + "/commands", json={"requestId": "resume", "command": "resume"})
        assert resumed.status_code == 200, resumed.text
        done = client.get(
            route + "/invocations/" + accepted.json()["invocationId"],
            params={"wait": True, "timeoutSeconds": 2},
        )
        assert done.json()["status"] == "succeeded", done.text
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["events"]
        snapshots = [
            change["values"]["materialSnapshot"]
            for event in events
            if event["message"]["type"] == "entity.observations"
            for change in event["message"]["changes"]
            if change["key"] == "/workcell" and "materialSnapshot" in change["values"]
        ]
        assert any(row["controlRequests"][0]["epoch"] == 1 for row in snapshots if row["controlRequests"])
        confirm = client.post(
            route + "/actions",
            json={
                "requestId": "confirm",
                "entityKey": "/workcell",
                "action": "confirmControl",
                "wait": True,
                "timeoutSeconds": 2,
                "params": {"owner": "auto", "epoch": 1, "operationId": "confirm"},
            },
        )
        assert confirm.status_code == 200 and confirm.json()["status"] == "succeeded", confirm.text
        completed = client.post(
            route + "/actions",
            json={
                "requestId": "recorded-wait",
                "entityKey": "/workcell",
                "action": "wait",
                "wait": True,
                "timeoutSeconds": 2,
                "params": {"owner": "human", "epoch": 1, "operationId": "recorded-op", "seconds": 0},
            },
        )
        assert completed.status_code == 200 and completed.json()["status"] == "succeeded", completed.text
        receipt = completed.json()["result"]["receipt"]
        history = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["events"]
        archived = [
            row["message"]["data"]["receipt"]
            for row in history
            if row["message"]["type"] == "entity.event" and row["message"]["event"] == "operationCompleted"
        ]
        assert archived == [receipt]
        assert client.delete(route).status_code == 204
        base = f"/recordings/{session['sessionId']}"
        metadata = client.get(base + "/recording.json").json()
        recorded = [
            json.loads(record.payload)
            for segment in metadata["segments"]
            for record in read_segment(client.get(base + "/" + segment["file"]).content).records
            if record.kind == 2
        ]
        recorded_receipts = [
            row["data"]["receipt"]
            for row in recorded
            if row["type"] == "entity.event" and row["event"] == "operationCompleted"
        ]
        assert recorded_receipts == [receipt]
