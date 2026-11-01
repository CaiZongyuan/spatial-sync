"""Real HTTP/Plan/recording bridge with explicit rotor feedback, not Newton proof."""

import asyncio
import json
from time import monotonic
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from apps.cli.lw.client import GatewayClient
from packages.info.log import read_segment
from tests.fixtures.centrifuge_cycles import TUBES, build_cycle_host
from tools.cell_passage_audit import collect_cycle_evidence


@pytest.fixture(scope="module", params=[True, False])
def cycle_gateway(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("cycle-http")

    class CycleSource(SyntheticSource):
        async def start(self, scene_id, options):
            self.options = options.model_copy()
            self.host, self.actuator, self.manifest = build_cycle_host(root / self.session_id,
                                                                     legacy=options.seed == 7)
            self.manifest.sessionId = self.session_id
            self.manifest.sceneRevision = "cycle-" + self.session_id
            self.catalog = self.host.catalog
            self.catalog.sceneRevision = self.manifest.sceneRevision
            self._state = "RUNNING"
            self._publish("stream.snapshot")
            self._last_clock = monotonic()
            self._task = asyncio.create_task(self._run())
            return self.manifest, self.catalog

        def _poses(self):
            return []

        def _advance(self, count):
            self._step_index += count
            due = int(self.sim_time * self.options.controlHz + 1e-9)
            while self._control_index < due:
                self._control_index += 1
                dt = 1 / self.options.controlHz
                self.actuator.advance(dt)
                for event in self.host.tick(self._control_index * dt, dt):
                    self._events.put_nowait(event)

    with pytest.MonkeyPatch.context() as env:
        env.setenv("LW_LIBRARY", str(root / "library"))
        app = create_app(Settings(state_root=root / "state", recordings_root=root / "recordings",
                                  recordings_enabled=request.param,
                                  node_trainer=(), live_bindings=None,
                                  node_token=None, node_capabilities=None),
                         source_factory=lambda kind, sid: CycleSource(sid))
        with TestClient(app) as client:
            yield client, request.param, root


def test_two_plan_bound_cycle_results_survive_handoff_and_live_history_eviction(cycle_gateway):
    client, recording, root = cycle_gateway
    session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})
    assert session.status_code == 201, session.text
    sid = session.json()["sessionId"]
    route = f"/api/v1/sessions/{sid}"

    def command(name, **args):
        response = client.post(route + "/commands", json={"requestId": uuid4().hex,
                                                          "command": name, "args": args})
        assert response.status_code == 200, response.text
        assert response.json()["ack"]["status"] == "applied", response.text

    def action(entity, name, params, **reference):
        return client.post(route + "/actions", json={"requestId": uuid4().hex, "entityKey": entity,
                                                     "action": name, "params": params, **reference})

    try:
        command("pause")
        results, plans, snapshots = [], [], {}
        for batch in range(2):
            if batch:
                assert action("/control", "load", {"batch": 1}).status_code == 202
                command("step", count=24)
            params = {"rpm": 500, "durationSeconds": 2, "owner": "auto", "epoch": 0,
                      "operationId": f"batch-{batch}"}
            plan = client.post("/api/v1/plans", json={"title": f"Batch {batch}", "target": "session",
                "steps": [{"id": 1, "do": {"entity": "/cent", "action": "start", "params": params}}]})
            assert plan.status_code == 201, plan.text
            plan = plan.json()
            plans.append(plan["id"])
            accepted = action("/cent", "start", params, planId=plan["id"], stepId=1, planRevision=1)
            assert accepted.status_code == 202, accepted.text
            identity = accepted.json()["invocationId"]
            for _ in range(10):
                command("step", count=960)
            current = client.get(f"/api/v1/plans/{plan['id']}").json()
            assert current["steps"][0]["status"] == "done", current
            assert current["stepResults"]["1"]["invocationId"] == identity
            terminal = client.get(route + "/invocations/" + identity)
            assert terminal.status_code == 200, terminal.text
            terminal = terminal.json()
            assert terminal["status"] == "succeeded", terminal
            assert terminal["result"]["completedLoad"] == TUBES[batch * 4:(batch + 1) * 4]
            results.append(terminal)
            snapshot = client.get(route + "/state", params={"keys": "/cent"}).json()
            snapshots[str(batch + 3)] = {"state": snapshot}
        assert action("/control", "handoff", {"owner": "human"}).status_code == 202
        command("step", count=24)
        # Exercise more than the Host's 50-entry recent window through real
        # fixture invocations, before evicting the gateway's 1000 terminals.
        for _ in range(55):
            completed = action("/control", "load", {"batch": 1})
            assert completed.status_code == 202, completed.text
            command("step", count=24)
            observed = client.get(route + "/invocations/" + completed.json()["invocationId"])
            assert observed.json()["status"] == "succeeded", observed.text
        for i in range(1005):
            rejected = action("/cent", "start", {"rpm": -1, "durationSeconds": 1,
                                                  "owner": "human", "epoch": 1, "operationId": str(i)})
            assert rejected.status_code == 422, rejected.text
        for plan_id, original in zip(plans, results, strict=True):
            plan = client.get("/api/v1/plans/" + plan_id).json()
            identity = plan["stepResults"]["1"]["invocationId"]
            recovered = client.get(route + "/invocations/" + identity)
            if recording:
                assert recovered.status_code == 200, recovered.text
                assert recovered.json() == original
            else:
                assert recovered.status_code == 503, recovered.text
                assert recovered.json()["detail"]["code"] == "history_unavailable"

        class PublicHTTP(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                response = client.request(request.method, str(request.url), content=request.content,
                                          headers=request.headers)
                return httpx.Response(response.status_code, headers=response.headers,
                                      content=response.content)

        async def refreshed_client_audit(stage_results=None):
            async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP()) as http:
                gateway = GatewayClient(session=sid, http=http)
                target = {key: session.json()[key] for key in ("sessionId", "sceneRevision", "epoch")}
                return await collect_cycle_evidence(
                    gateway, {"plans": [{"id": identity, "stage": stage}
                                         for stage, identity in zip((3, 4), plans, strict=True)],
                              "target": target, "stageResults": stage_results or {}},
                    {3: TUBES[:4], 4: TUBES[4:]}, entity="/cent",
                )

        audit = asyncio.run(refreshed_client_audit())
        assert audit["status"] == ("cycle_evidence_valid" if recording else "unavailable"), audit
        if recording:
            assert all(row["stageSnapshotAvailable"] is False for row in audit["batches"].values())
            assert asyncio.run(refreshed_client_audit(snapshots))["status"] == "cycle_evidence_valid"
            snapshots["3"]["state"]["entities"]["/cent"]["values"]["actualRotorTurns"] += 1
            assert asyncio.run(refreshed_client_audit(snapshots))["status"] == "invalid"
    finally:
        assert client.delete(route).status_code == 204
    if recording:
        metadata = client.get(f"/recordings/{sid}/recording.json").json()
        assert metadata["status"] == "complete"
        retained = {}
        for segment in metadata["segments"]:
            response = client.get(f"/recordings/{sid}/" + segment["file"])
            assert response.status_code == 200
            decoded = read_segment(response.content)
            assert not decoded.truncated
            for row in decoded.records:
                if row.kind != 1:
                    value = json.loads(row.payload)
                    if value.get("type") == "invocation.update" and value.get("status") == "succeeded":
                        retained[value["invocationId"]] = value.get("result")
        for terminal in results:
            assert retained[terminal["invocationId"]] == terminal["result"]
        # Filesystem fault at the real archive reader, after reliable close:
        # the generic HTTP lookup must report absence rather than recreate evidence.
        moved = []
        try:
            owned = (root / "recordings" / sid).resolve()
            for segment in metadata["segments"]:
                path = (owned / segment["file"]).resolve()
                assert path.is_relative_to(owned)
                unavailable = path.with_suffix(path.suffix + ".unavailable")
                path.rename(unavailable)
                moved.append((path, unavailable))
            for terminal in results:
                missing = client.get(route + "/invocations/" + terminal["invocationId"])
                assert missing.status_code == 503, missing.text
                assert missing.json()["detail"]["code"] == "history_unavailable"
        finally:
            for path, unavailable in moved:
                unavailable.rename(path)


def test_legacy_none_completion_still_round_trips_through_http(cycle_gateway):
    client, _, _ = cycle_gateway
    response = client.post("/api/v1/sessions", json={"sceneId": "lab-demo", "seed": 7})
    assert response.status_code == 201, response.text
    route = "/api/v1/sessions/" + response.json()["sessionId"]

    def command(name, **args):
        response = client.post(route + "/commands", json={"requestId": uuid4().hex,
                                                          "command": name, "args": args})
        assert response.json()["ack"]["status"] == "applied", response.text

    try:
        command("pause")
        accepted = client.post(route + "/actions", json={"requestId": uuid4().hex, "entityKey": "/cent",
            "action": "start", "params": {"rpm": 500, "durationSeconds": 1}})
        assert accepted.status_code == 202, accepted.text
        for _ in range(10):
            command("step", count=960)
        terminal = client.get(route + "/invocations/" + accepted.json()["invocationId"]).json()
        assert terminal["status"] == "succeeded" and terminal["result"] is None
    finally:
        assert client.delete(route).status_code == 204
