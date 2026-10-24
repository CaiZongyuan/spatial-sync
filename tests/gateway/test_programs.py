"""Uploaded script bytes remain data in every gateway package operation."""

import io
import math
import tarfile

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.training.inputs import pack_package
from tests.fixtures.script_program import SOURCE, program_directory


def test_http_program_snapshot_and_source_read_never_import_code_or_start_a_source(tmp_path):
    calls = []

    def forbidden(*args):
        calls.append(args)
        raise AssertionError("Package operations must not create a Source")

    package, manifest = program_directory(tmp_path)
    content = pack_package(package)
    headers = {"Content-Type": "application/x-tar", "X-Actor": "agent:owned-author"}
    with TestClient(create_app(source_factory=forbidden)) as client:
        inspected = client.post("/api/v1/programs/inspect", content=content, headers=headers)
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()["metadata"] == manifest
        pushed = client.post("/api/v1/programs", content=content, headers=headers)
        assert pushed.status_code == 201, pushed.text
        record = pushed.json()
        assert record["id"] == "program/robot_arm.script.probe@1"
        assert record["contentHash"] == inspected.json()["contentHash"]
        assert client.post("/api/v1/programs", content=content, headers=headers).json()["idempotent"]
        shown = client.get("/api/v1/programs/probe/1")
        assert shown.status_code == 200 and shown.json()["contentHash"] == record["contentHash"]
        source = client.get("/api/v1/programs/probe/1/files/controller.py")
        assert source.status_code == 200 and source.content == SOURCE
        assert source.headers["Content-Type"].startswith("text/plain")
        assert client.get("/api/v1/programs/probe/1/archive").content == content
        assert len(client.get("/api/v1/programs").json()["programs"]) == 1
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
        assert client.get("/api/v1/jobs").json()["jobs"] == []
        assert not calls and not hasattr(math, "_lw_uploaded_program_marker")


def test_program_push_rejects_internal_imports_invalid_config_and_escaping_archives(tmp_path):
    package, manifest = program_directory(tmp_path)
    headers = {"Content-Type": "application/x-tar"}
    with TestClient(create_app()) as client:
        (package / "controller.py").write_bytes(b"import newton\n" + SOURCE)
        forbidden = client.post("/api/v1/programs", content=pack_package(package), headers=headers)
        assert forbidden.status_code == 422 and forbidden.json()["detail"]["code"] == "program_invalid"
        (package / "controller.py").write_bytes(SOURCE)
        manifest["config"] = {"undeclared": True}
        (package / "program.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        invalid = client.post("/api/v1/programs", content=pack_package(package), headers=headers)
        assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "program_invalid"
        escaped = io.BytesIO()
        with tarfile.open(fileobj=escaped, mode="w") as archive:
            entry = tarfile.TarInfo("../outside.py")
            entry.size = len(SOURCE)
            archive.addfile(entry, io.BytesIO(SOURCE))
        response = client.post("/api/v1/programs", content=escaped.getvalue(), headers=headers)
        assert response.status_code == 422
        assert client.get("/api/v1/programs").json()["programs"] == []
        assert not hasattr(math, "_lw_uploaded_program_marker")
