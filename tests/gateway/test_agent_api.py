"""Agent public routes work without a Session and never expose private suite inputs."""

import io
import json
import tarfile

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings


def no_source(*args):
    raise AssertionError("Agent metadata and Harness operations must not create a Source")


def test_snapshot_export_import_and_new_agent_work_with_zero_sessions_and_survive_restart():
    app = create_app(source_factory=no_source)
    with TestClient(app) as client:
        agent = client.get("/api/v1/agents/lab-operator").json()
        assert agent["model"] is agent["thinking"] is None
        assert client.get("/api/v1/agents").json() == {"agents": [agent]}
        exported = client.get("/api/v1/agents/lab-operator/versions/1/snapshot")
        assert exported.status_code == 200 and exported.headers["content-type"] == "application/x-tar"
        with tarfile.open(fileobj=io.BytesIO(exported.content)) as archive:
            assert set(archive.getnames()) == {"AGENTS.md", "skills.lock", "mcp.yaml", "hooks/README.md"}
        created = client.post("/api/v1/agents?id=clone", content=exported.content,
                              headers={"Content-Type": "application/x-tar"})
        assert created.status_code == 201, created.text
        assert created.json()["referenceVersion"] == created.json()["currentVersion"] == 1
        assert created.json()["model"] is None
        snapshot = client.post("/api/v1/agents/clone/snapshots")
        assert snapshot.status_code == 201 and snapshot.json()["version"] == 1
        imported = client.post("/api/v1/agents/clone/snapshots", content=exported.content,
                               headers={"Content-Type": "application/x-tar"})
        assert imported.json() == snapshot.json()
        versions = client.get("/api/v1/agents/clone/versions").json()["versions"]
        assert versions == [snapshot.json()]
        assert client.get("/api/v1/agents/clone/versions/1?against=1").json()["diff"]["changes"] == []
        assert client.get("/api/v1/health").json()["sessions"] == []
        assert client.post("/api/v1/agents?id=clone", content=exported.content,
                           headers={"Content-Type": "application/x-tar"}).status_code == 409
    with TestClient(create_app(source_factory=no_source)) as restarted:
        assert restarted.get("/api/v1/agents/clone").json() == created.json()
        assert restarted.get("/api/v1/agents/clone/versions/1/snapshot").content == exported.content


def test_public_agent_suite_hides_private_inputs_from_every_role_and_rejects_unsupported_run():
    with TestClient(create_app(source_factory=no_source)) as client:
        for headers in ({}, {"X-Role": "builder", "X-Actor": "user:builder"}, {"X-Role": "evaluator"}):
            listed = client.get("/api/v1/suites", headers=headers).json()["suites"]
            public = client.get("/api/v1/suites/lab-ops-basic/1", headers=headers).json()
            assert public in listed and len(public["cases"]) == 10
            serialized = json.dumps(public)
            for private in ("held-out-safe-sensor", "grading/", "private/", "weight", "when", "threshold"):
                assert private not in serialized
            rows = client.get("/api/v1/suites/lab-ops-basic/1/scoreboard", headers=headers).json()
            assert rows == {"rows": []}
            denied = client.post("/api/v1/evaluations", headers=headers,
                                 json={"subject": "agent/lab-operator", "suite": "suite/lab-ops-basic@1"})
            assert denied.status_code == 422 and denied.json()["detail"]["code"] == "agent_runner_unavailable"
            test = client.post("/api/v1/evaluations", headers=headers,
                               json={"subject": "agent/lab-operator", "suite": "suite/lab-ops-basic@1",
                                     "split": "test"})
            assert test.status_code == 403 and test.json()["detail"]["code"] == "test_requires_promotion"
        assert client.get("/api/v1/evaluations").json() == {"evaluations": []}
        assert client.get("/api/v1/health").json()["sessions"] == []
        private_file = "/api/v1/library/files/builtin/suites/lab-ops-basic/1/private/centrifuge-two.yaml"
        assert client.get(private_file).status_code == 404
        assert client.get("/api/v1/suites/lab-ops-basic/1/grading/centrifuge-two").status_code == 404
        assert client.get("/api/v1/suites/lab-ops-basic/1/cases/held-out-safe-sensor").status_code == 404
        assert client.post("/api/v1/suites/lab-ops-basic/1/grade", json={"score": 100}).status_code == 404


def test_mutation_permissions_and_invalid_transport_cannot_leak_submitted_credentials():
    settings = Settings(remote=True, operator_token="operator-token", viewer_token="viewer-token")
    with TestClient(create_app(settings, source_factory=no_source)) as client:
        denied = client.post("/api/v1/agents/lab-operator/snapshots", headers={
            "Authorization": "Bearer viewer-token", "X-Role": "operator", "X-Actor": "user:operator"
        })
        assert denied.status_code == 403
        operator = {"Authorization": "Bearer operator-token"}
        canary = "private-canary-864209"
        for content_type in ("application/json", "application/x-tar", ""):
            headers = operator | ({"Content-Type": content_type} if content_type else {})
            response = client.post("/api/v1/agents/lab-operator/snapshots", content=canary, headers=headers)
            assert response.status_code == 422
            assert canary not in response.text
        for suffix in (f"/{canary}", f"/1?against={canary}", f"/{canary}/snapshot"):
            response = client.get("/api/v1/agents/lab-operator/versions" + suffix, headers=operator)
            assert response.status_code == 404 and canary not in response.text
