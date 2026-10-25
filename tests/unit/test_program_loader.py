"""Controlled owned worker checks Python loading only; actual Newton evidence is separate."""

import math
import multiprocessing as mp
import traceback
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.library.common import LibraryError
from packages.programs.authorization import require_program_approval
from packages.programs.loading import ProgramLoader
from packages.training.inputs import pack_package
from tests.fixtures.embodied_evaluation import native_suite
from tests.fixtures.script_program import SOURCE
from tests.gateway.test_program_plans import prepare


def worker(connection, proof, state_root):
    loader = None
    try:
        loader = ProgramLoader("loader-fixture", state_root=Path(state_root))
        prepared = loader.load_evaluation(proof)
        prepared.delegate.tick(None, 0)
        result = {
            "loaded": prepared.delegate.id,
            "contentHash": prepared.record["contentHash"],
            "marker": getattr(math, "_lw_uploaded_program_marker", False),
        }
        if hasattr(prepared.delegate, "helper_value"):
            result["helperValue"] = prepared.delegate.helper_value
        connection.send(result)
    except BaseException as error:
        connection.send(
            {
                "error": str(error),
                "traceback": traceback.format_exc(),
                "marker": getattr(math, "_lw_uploaded_program_marker", False),
                "constructor": getattr(math, "_lw_uploaded_constructor_marker", False),
            }
        )
    finally:
        if loader is not None:
            loader.close()
        connection.close()


def test_gateway_context_rejects_before_any_candidate_import(tmp_path):
    with pytest.raises(LibraryError, match="Runner"):
        ProgramLoader("loader-fixture", state_root=tmp_path)
    assert not hasattr(math, "_lw_uploaded_program_marker")


@pytest.mark.parametrize("case", ["loaded", "relative", "config", "revoked", "async_init", "async_tick"])
def test_owned_loader_worker_rechecks_approval_and_config_without_mutating_snapshot(tmp_path, case):
    def forbidden(*_):
        raise AssertionError("Loader contract tests start no physics Source")

    with TestClient(create_app(source_factory=forbidden)) as client:
        body, snapshot = prepare(client, tmp_path)
        if case in ("config", "relative", "async_init", "async_tick"):
            directory = tmp_path / "program"
            manifest = yaml.safe_load((directory / "program.yaml").read_text(encoding="utf-8"))
            if case == "config":
                source = SOURCE.replace(
                    b"    Config = ProgramConfig",
                    b"    class Config(ProgramConfig):\n        undeclared: int = 1",
                )
                source += b"\n    def __init__(self):\n        math._lw_uploaded_constructor_marker = True\n"
            elif case == "relative":
                (directory / "helper.py").write_text("VALUE = 3\n", encoding="utf-8")
                source = SOURCE.replace(
                    b"def tick(self, ctx, dt): pass",
                    b"def tick(self, ctx, dt):\n        from .helper import VALUE\n"
                    b"        self.helper_value = VALUE",
                )
            else:
                method = "init(self, ctx)" if case == "async_init" else "tick(self, ctx, dt)"
                source = SOURCE.replace(
                    ("def " + method + ": pass").encode(),
                    ("async def " + method + ": yield None").encode(),
                )
                source += b"\n    def __init__(self):\n        math._lw_uploaded_constructor_marker = True\n"
            (directory / "controller.py").write_bytes(source)
            (directory / "program.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
            pushed = client.post(
                "/api/v1/programs",
                content=pack_package(directory),
                headers={"Content-Type": "application/x-tar"},
            )
            assert pushed.status_code == 201, pushed.text
            snapshot = pushed.json()
            body["program"] = {"id": snapshot["id"], "contentHash": snapshot["contentHash"]}
        plan = client.post("/api/v1/plans", json=body).json()
        assert (
            client.post(
                "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        manager = client.app.state.manager
        scene = manager.library.resolve(body["evaluation"]["scene"], launch=True, source="newton")
        suite = native_suite(manager, scene)
        proof, _ = require_program_approval(manager, snapshot["id"], suite, scene, plan_id=plan["id"])
        record, package = manager.programs.resolve(snapshot["id"])
        before = {
            path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*")
            if path.is_file()
        }
        if case == "revoked":
            assert (
                client.post(
                    "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "revoke"}
                ).status_code
                == 200
            )
        parent, child = mp.get_context("spawn").Pipe(duplex=False)
        process = mp.get_context("spawn").Process(
            target=worker,
            args=(child, proof.model_dump(mode="json"), str(manager.settings.state_root)),
            name="newton-loader-fixture",
        )
        process.start()
        child.close()
        try:
            assert parent.poll(15), "Owned loader worker did not answer"
            result = parent.recv()
            process.join(5)
            assert process.exitcode == 0, result
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()
        if case in ("loaded", "relative"):
            if case == "relative":
                assert result.pop("helperValue") == 3
            assert result == {
                "loaded": "robot_arm.script.probe",
                "contentHash": record["contentHash"],
                "marker": True,
            }
        else:
            assert "error" in result and result["constructor"] is False, result
            assert result["marker"] is (case in ("config", "async_init", "async_tick")), result
        assert before == {
            path.relative_to(package).as_posix(): path.read_bytes()
            for path in package.rglob("*")
            if path.is_file()
        }
        assert not hasattr(math, "_lw_uploaded_program_marker")
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
