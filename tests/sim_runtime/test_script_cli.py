"""Installed lw and official stdio MCP execute the approved script over owned HTTP/Newton CPU."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import httpx
import pytest
import yaml
from mcp import Client, Implementation, StdioServerParameters

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from packages.library.common import BUILTIN_ROOT
from packages.library.scenes import SceneLibrary
from packages.policies.subjects import fixed_bindings
from tests.cli.cli_support import LW, ROOT, result, stop
from tests.fixtures.embodied_evaluation import native_suite
from tests.fixtures.script_program import closing_program_directory
from tests.fixtures.training_task import task_directory, training_plan


async def test_installed_cli_and_mcp_use_real_script_evaluator_recordings_and_notebook(
    tmp_path, record_property
):
    library, state = tmp_path / "library", tmp_path / "state"
    scenes = SceneLibrary(user_root=library, state_root=state)
    scene = scenes.resolve("robot-and-tubes@2", launch=True, source="newton")
    manager = SimpleNamespace(
        library_assets=SimpleNamespace(roots={"builtin": BUILTIN_ROOT, "user": library})
    )
    suite = native_suite(manager, scene, name="script-cli-engineering", timeout=45.0)
    fingers = fixed_bindings(scene, "newton")["/World/Lab/RobotA"]["config"]["fingerKeys"]
    directory = closing_program_directory(tmp_path / "script", fingers)
    task = task_directory(tmp_path / "task", "script-cli")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = os.environ | {
        "LW_URL": url,
        "LW_SESSION": "",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join((str(ROOT / "apps/cli"), str(ROOT))),
        "LW_LIBRARY": str(library),
        "SPATIAL_STATE_ROOT": str(state),
        "SPATIAL_RECORDINGS_ROOT": str(tmp_path / "recordings"),
        "SPATIAL_NODE_TRAINER": "[]",
    }
    assert LW.is_file(), "Use the installed project entrypoint"
    cli_module = subprocess.check_output(
        [sys.executable, "-c", "import lw; print(lw.__file__)"], env=env, text=True
    )
    assert str(ROOT / "apps/cli") in cli_module

    def invoke(*arguments):
        run = subprocess.run(
            [str(LW), *map(str, arguments), "--json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert run.returncode == 0, run.stdout + run.stderr
        return json.loads(run.stdout)

    with (tmp_path / "gateway.log").open("wb") as log:
        gateway = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "apps.api.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(base_url=url, timeout=2, trust_env=False) as http:
                deadline = time.monotonic() + 15
                while True:
                    assert gateway.poll() is None, (tmp_path / "gateway.log").read_text(encoding="utf-8")
                    try:
                        if http.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.02)
                program = invoke("program", "push", directory)
                pinned = invoke("task", "push", task)
                body = training_plan(
                    pinned["id"],
                    algorithm={},
                    model={},
                    hyperparameters={},
                    jobs={"seeds": []},
                    program={"id": program["id"], "contentHash": program["contentHash"]},
                    budget={"gpuHours": 0, "episodes": 4},
                    evaluation={"scene": scene.id, "episodes": 1, "threshold": 1},
                    interview="interview.md",
                )
                (tmp_path / "interview.md").write_text(
                    "Human engineering approval of the fixed CPU script probe.", encoding="utf-8"
                )
                path = tmp_path / "plan.yaml"
                path.write_text(yaml.safe_dump(body), encoding="utf-8")
                plan = invoke("plan", "submit", path)
                assert (
                    http.post(
                        "/api/v1/approvals/" + plan["approvalId"] + "/decision",
                        json={"decision": "approve"},
                        headers={"X-Actor": "user:owned-code-reviewer"},
                    ).status_code
                    == 200
                )
            first = invoke(
                "eval", "run", program["id"], "--suite", suite.id, "--plan", plan["id"], "--revision", 1
            )
            deadline = time.monotonic() + 60
            while True:
                first = invoke("eval", "show", first["evaluationId"])
                if first["status"] in ("completed", "failed", "cancelled"):
                    break
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            assert first["status"] == "completed", first
            async with Client(
                StdioServerParameters(command=str(LW), args=["mcp"], env=env),
                mode="legacy",
                client_info=Implementation(name="script-runtime-counter", version="1"),
            ) as mcp:
                assert (await result(mcp, "program_show", id=program["id"]))["contentHash"] == program[
                    "contentHash"
                ]
                second = await result(
                    mcp,
                    "evaluation_run",
                    subject=program["id"],
                    suite=suite.id,
                    planId=plan["id"],
                    planRevision=1,
                )
                deadline = time.monotonic() + 60
                while True:
                    second = await result(mcp, "evaluation_show", id=second["evaluationId"])
                    if second["status"] in ("completed", "failed", "cancelled"):
                        break
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.05)
                assert second["status"] == "completed", second
                notebook = await result(mcp, "notebook_show", goal=plan["goalId"])
                assert {entry["evaluationId"] for entry in notebook["entries"]} == {
                    first["evaluationId"],
                    second["evaluationId"],
                }
            receipts = []
            for run in (first, second):
                assert run["candidateIdentity"]["contentHash"] == program["contentHash"]
                execution = run["episodes"][0]["programExecution"]
                assert execution["actionStarts"] == 1 and execution["callbacks"]["tick"] > 0
                assert run["result"]["successes"] == 0
                receipts.append(
                    {
                        "evaluationId": run["evaluationId"],
                        "execution": execution,
                        "recordingId": run["episodes"][0]["recordingId"],
                    }
                )
            assert invoke("job", "ls") == []
            record_property(
                "actual_installed_script_clients",
                json.dumps(
                    {
                        "program": program["id"],
                        "runs": receipts,
                        "cliModule": cli_module.strip(),
                        "scriptCreatedJobs": 0,
                    },
                    sort_keys=True,
                ),
            )
        finally:
            stop(gateway)
