"""Actual new interpreters measure core References against owned dirty source snapshots."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from zipfile import ZipFile

import httpx

from tests.gateway.test_evaluation_deadlines import deadline_suite

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = """from pathlib import Path
from apps.api.app.main import create_app
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system
from apps.api.app.settings import Settings
from packages.library.common import REPO_ROOT
import apps.api.app.evaluations as evaluator
import packages.programs.robot_arm.virtual as program
import packages.info.evaluation_identity as identity
class Sampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Owned identity fixture; no host probes"))
app = create_app(Settings(node_trainer=()), platform_sampler=Sampler())
@app.get("/fixture/imports")
async def imports():
    return {"root":str(REPO_ROOT), "evaluator":evaluator.__file__,
            "program":program.__file__, "identity":identity.__file__}
@app.get("/fixture/recovery/{identifier}")
async def recovery(identifier:str):
    record=app.state.manager.evaluations.store.records[identifier]
    return record.get("identitySnapshot")
"""


def test_actual_core_reference_survives_candidate_only_restart_but_not_helper_or_judge_changes(tmp_path):
    project = tmp_path / "owned-project"
    project.mkdir()
    for relative in (
        "apps/api",
        "apps/cli",
        "apps/node",
        "packages",
        "devices",
        "library",
        "assets",
        "catalog",
        "skills",
    ):
        destination = project / relative
        assert destination.resolve().is_relative_to(tmp_path.resolve())
        shutil.copytree(ROOT / relative, destination, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("uv.lock", "pyproject.toml"):
        shutil.copyfile(ROOT / name, project / name)
    (project / "identity_fixture_gateway.py").write_text(GATEWAY, encoding="utf-8")
    user_library = tmp_path / "user-library"
    suite = deadline_suite(user_library)
    state, recordings = tmp_path / "state", tmp_path / "recordings"
    env = os.environ | {
        "PYTHONPATH": str(project),
        "LW_LIBRARY": str(user_library),
        "SPATIAL_STATE_ROOT": str(state),
        "SPATIAL_RECORDINGS_ROOT": str(recordings),
        "SPATIAL_NODE_TRAINER": "[]",
        "SPATIAL_LIVE_BINDINGS": "",
        "SPATIAL_REMOTE": "0",
    }
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    helper = ROOT / "tests/fixtures/owned_processes.py"
    log = (tmp_path / "owned-gateway.log").open("w", encoding="utf-8")
    owned = []

    def stop_all():
        for process, identities in reversed(owned):
            if process.poll() is None:
                with subprocess.Popen(
                    [sys.executable, str(helper), "stop", identities],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ) as stopping:
                    # The creating parent must reap its child while the verified
                    # helper waits; a POSIX zombie cannot disappear otherwise.
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    stdout, stderr = stopping.communicate(timeout=10)
                    assert stopping.returncode == 0, stderr
                    assert json.loads(stdout)["alive"] == []

    def start(http):
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "identity_fixture_gateway:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=project,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        for _ in range(300):
            assert process.poll() is None, "Owned source-copy gateway exited before readiness"
            try:
                if http.get("/api/v1/health", timeout=0.2).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.025)
        else:
            process.terminate()
            process.wait(timeout=5)
            raise AssertionError("Owned source-copy gateway never became ready")
        identities = subprocess.run(
            [sys.executable, str(helper), "snapshot", str(process.pid)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        owned.append((process, identities))
        imports = http.get("/fixture/imports").json()
        assert Path(imports["root"]).resolve() == project.resolve()
        assert all(
            Path(imports[name]).resolve().is_relative_to(project.resolve())
            for name in ("evaluator", "program", "identity")
        )

    def evaluate(http):
        response = http.post("/api/v1/evaluations", json={"subject": "robot_arm.virtual", "suite": suite})
        assert response.status_code == 202, response.text
        identifier = response.json()["evaluationId"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = http.get("/api/v1/evaluations/" + identifier).json()
            if result["status"] in ("completed", "failed"):
                break
            time.sleep(0.025)
        assert result["status"] == "completed", result.get("error")
        assert result["result"]["episodes"] == 2 and result["result"]["score"] == 0
        return result["result"]

    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=15) as http:
            start(http)
            first = evaluate(http)
            assert first["decision"] == "baseline" and first["reference"] is None
            recovery = http.get("/fixture/recovery/" + first["evaluationId"]).json()
            program_path = project / "packages/programs/robot_arm/virtual.py"
            original = program_path.read_bytes()
            with ZipFile(state / "evaluations/identity-inputs" / recovery["recovery"]["file"]) as archive:
                assert archive.read("code/packages/programs/robot_arm/virtual.py") == original
                assert archive.read("code/uv.lock") == (project / "uv.lock").read_bytes()
            stop_all()

            text = program_path.read_text(encoding="utf-8")
            program_path.write_text(
                text.replace(
                    "class RobotArmProgram:\n",
                    "class RobotArmProgram:\n    # Candidate-only source revision\n",
                    1,
                ),
                encoding="utf-8",
            )
            start(http)
            candidate = evaluate(http)
            assert candidate["version"] != first["version"]
            assert candidate["reference"] == first["evaluationId"]
            assert candidate["conditionsGroup"] == first["conditionsGroup"]
            stop_all()

            text = program_path.read_text(encoding="utf-8")
            program_path.write_text(
                text.replace(
                    "    yaw, shoulder, elbow, *wrist = joints\n",
                    "    yaw, shoulder, elbow, *wrist = joints\n    yaw += 0.0\n",
                    1,
                ),
                encoding="utf-8",
            )
            start(http)
            environment = evaluate(http)
            assert environment["reference"] is None and environment["decision"] == "baseline"
            assert environment["conditionsGroup"] != first["conditionsGroup"]
            stop_all()

            judge_path = project / "packages/info/conditions.py"
            judge_source = judge_path.read_text(encoding="utf-8")
            judge_path.write_text(
                judge_source.replace(
                    "    results = [item[3] for item in compiled.results(entity_key, values, metadata)]\n",
                    "    compiled = CompiledConditions(list(compiled))\n"
                    "    results = [item[3] for item in compiled.results(entity_key, values, metadata)]\n",
                    1,
                ),
                encoding="utf-8",
            )
            start(http)
            judge = evaluate(http)
            assert judge["reference"] is None and judge["decision"] == "baseline"
            assert judge["conditionsGroup"] != environment["conditionsGroup"]
            remeasured = evaluate(http)
            assert remeasured["reference"] == judge["evaluationId"]
            assert remeasured["conditionsGroup"] == judge["conditionsGroup"]
            prior = http.get("/api/v1/evaluations/" + first["evaluationId"]).json()["result"]
            assert prior["score"] == first["score"] and prior["decision"] == first["decision"]
            assert "comparisonIdentity" not in json.dumps(prior) and "identitySnapshot" not in json.dumps(
                prior
            )
    finally:
        stop_all()
        log.close()
        print((tmp_path / "owned-gateway.log").read_text(encoding="utf-8")[-7000:])
