"""Installed CLI and official MCP share immutable observation evidence over real HTTP."""

import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, Implementation, StdioServerParameters

from packages.vault import Vault
from tests.fixtures.policy import observation_spec
from tests.fixtures.policy_evidence import observation_artifacts, sampler_task
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import NODE


async def test_cli_and_official_mcp_persist_and_verify_numerical_reports(
    gateway_url, gateway_directory, tmp_path, monkeypatch
):
    env = os.environ | {"LW_URL": gateway_url, "LW_SESSION": "", "PYTHONIOENCODING": "utf-8"}

    def invoke(*arguments):
        process = subprocess.run(
            [str(LW), *arguments, "--json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads(process.stdout)

    token = Vault(gateway_directory / "state/internal-node").adapter_environment({"NODE": "local-node"})[
        "NODE"
    ]
    runner = sampler_task(tmp_path / "task")
    monkeypatch.setenv("LW_TASK_PACKAGE", str(tmp_path / "task"))
    spec = observation_spec()
    spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = ["/World/Lab/RobotA/fr3/fr3_joint1"]
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        job = uploaded_job(
            http,
            tmp_path,
            node_headers=NODE | {"X-Node-Token": token},
            spec=spec,
            artifact_factory=lambda job: observation_artifacts(job, tmp_path / "output", runner),
        )
    policy = invoke("policy", "create", job["jobId"] + "/checkpoints/model_0.pt")
    check = invoke("policy", "check-observations", policy["id"], "--artifact", "observations/samples.json")
    assert check["result"]["numericalPass"] and check["qualified"] is False
    verified = invoke(
        "policy",
        "verify-observation-source",
        policy["id"],
        check["id"],
        "--artifact",
        "observations/capture.json",
    )
    assert verified["sourceVerification"]["status"] == "capture_contract_verified"
    assert invoke("policy", "observation-check", policy["id"], check["id"])["qualified"] is False
    assert len(invoke("policy", "observation-checks", policy["id"])) == 1
    async with Client(
        StdioServerParameters(command=str(LW), args=["mcp"], env=env),
        mode="legacy",
        client_info=Implementation(name="observation-agent", version="1"),
    ) as mcp:
        same = await result(
            mcp, "policy_check_observations", id=policy["id"], artifact="observations/samples.json"
        )
        assert same["idempotent"] and same["id"] == check["id"]
        assert len(await result(mcp, "policy_observation_checks", id=policy["id"])) == 1
        read = await result(mcp, "policy_observation_check_show", id=policy["id"], check=check["id"])
        assert read["sourceVerification"]["qualified"] is False
