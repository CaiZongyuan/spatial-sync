"""Installed lw and official MCP use the public session-free policy resources."""

import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, Implementation, StdioServerParameters

from packages.vault import Vault
from tests.gateway.test_policies import uploaded_job
from tests.gateway.test_training_jobs import NODE


async def test_installed_policy_cli_and_mcp_share_readback_and_rejection(
    gateway_url, gateway_directory, tmp_path
):
    env = os.environ | {"LW_URL": gateway_url, "LW_SESSION": "", "PYTHONIOENCODING": "utf-8"}

    def invoke(*arguments, code=0):
        process = subprocess.run(
            [str(LW), *arguments, "--json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert process.returncode == code, process.stdout + process.stderr
        return json.loads(process.stdout)

    assert invoke("policy", "ls") == []
    refused = invoke("policy", "create", "host-path", code=1)
    assert refused["error"]["detail"]["code"] == "policy_source_invalid"
    assert invoke("policy", "show", "policy/missing@1", code=1)["error"]["detail"]["code"] == "unknown_policy"
    # This test's own node credential; never read another gateway's Vault.
    token = Vault(gateway_directory / "state/internal-node").adapter_environment({"NODE": "local-node"})[
        "NODE"
    ]
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        job = uploaded_job(http, tmp_path, node_headers=NODE | {"X-Node-Token": token})
        checkpoint = job["jobId"] + "/checkpoints/model_0.pt"
        policy = invoke("policy", "create", checkpoint)
        assert policy["qualified"] is False and not policy["idempotent"]
        assert invoke("policy", "create", checkpoint)["idempotent"]
        assert invoke("policy", "show", policy["id"])["checkpointSha256"] == policy["checkpointSha256"]
    async with Client(
        StdioServerParameters(command=str(LW), args=["mcp"], env=env),
        mode="legacy",
        client_info=Implementation(name="policy-agent", version="1"),
    ) as mcp:
        names = {tool.name for tool in (await mcp.list_tools()).tools}
        assert {"policy_create", "policy_list", "policy_show"} <= names
        assert policy["id"] in [item["id"] for item in await result(mcp, "policy_list")]
        assert (await result(mcp, "policy_show", id=policy["id"]))["contentHash"] == policy["contentHash"]
        assert (await result(mcp, "policy_create", checkpoint=checkpoint))["idempotent"]
