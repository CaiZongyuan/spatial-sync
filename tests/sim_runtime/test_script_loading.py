"""Actual owned Newton CPU loading/readiness; this is separate from Suite quality evidence."""

import json
import math

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.sources.newton_source import NewtonSource
from packages.programs.script_subjects import resolve_program_subject, validate_script_catalog
from packages.protocol.schema import SessionOptions
from packages.training.inputs import pack_package
from tests.fixtures.embodied_evaluation import native_suite
from tests.fixtures.script_program import SOURCE
from tests.gateway.test_program_plans import prepare


@pytest.mark.parametrize("case", ["loaded", "init-error"])
async def test_actual_newton_loads_approved_script_and_rejects_failed_init_before_ready(
    tmp_path, case, record_property
):
    with TestClient(create_app()) as http:
        body, record = prepare(http, tmp_path)
        if case == "init-error":
            directory = tmp_path / "program"
            (directory / "controller.py").write_bytes(
                SOURCE.replace(
                    b"def init(self, ctx): pass",
                    b"def init(self, ctx):\n        raise RuntimeError('controlled actual init error')",
                )
            )
            pushed = http.post(
                "/api/v1/programs",
                content=pack_package(directory),
                headers={"Content-Type": "application/x-tar"},
            )
            assert pushed.status_code == 201, pushed.text
            record = pushed.json()
            body["program"] = {"id": record["id"], "contentHash": record["contentHash"]}
        response = http.post("/api/v1/plans", json=body)
        assert response.status_code == 201, response.text
        plan = response.json()
        assert (
            http.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        manager = http.app.state.manager
        scene = manager.library.resolve(body["evaluation"]["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene)
        subject = resolve_program_subject(
            manager, record["id"], suite, scene, plan_id=plan["id"], plan_revision=1
        )
        _, package = manager.programs.resolve(record["id"])
        before = {
            path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*")
            if path.is_file()
        }
        source = NewtonSource("session-script-" + case)
        source.configure_scene(scene)
        source.configure_evaluation(subject.document())
        source.configure_execution(state_root=manager.settings.state_root)
        evidence = {
            "case": case,
            "candidate": subject.candidateIdentity.model_dump(mode="json"),
            "gatewayImportMarker": hasattr(math, "_lw_uploaded_program_marker"),
        }
        try:
            if case == "init-error":
                with pytest.raises(
                    RuntimeError, match="initialization failed before Runner readiness"
                ) as error:
                    await source.start(scene.builder, SessionOptions(device="cpu"))
                evidence["actualFailure"] = str(error.value)
            else:
                manifest, catalog = await source.start(scene.builder, SessionOptions(device="cpu"))
                assert manifest.source.kind == "newton" and manifest.source.newtonVersion
                validate_script_catalog(catalog, subject)
                receipt = await source.program_execution(subject.target.entityKey)
                assert (
                    receipt["program"] == record["id"]
                    and receipt["programContentHash"] == record["contentHash"]
                )
                assert receipt["callbacks"]["init"] == 1 and receipt["actionStarts"] == 0
                assert receipt["callbackErrors"] == 0
                assert source.stats()["runnerPID"] and source.stats()["runnerAlive"]
                evidence.update(newtonVersion=manifest.source.newtonVersion, execution=receipt)
        finally:
            await source.close()
        assert not source.stats()["runnerAlive"] and source.stats()["readerThreadsAlive"] == []
        assert before == {
            path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*")
            if path.is_file()
        }
        assert not hasattr(math, "_lw_uploaded_program_marker")
        evidence["ownedCleanupComplete"] = True
        record_property("actual_script_runner_evidence", json.dumps(evidence, sort_keys=True))
