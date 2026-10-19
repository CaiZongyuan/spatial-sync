"""The actual installed CLI and official MCP stdio share metadata, never Vault values."""

import json
import os
import subprocess

import httpx
import pytest
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters

from packages.vault import Vault

SENTINEL = "FAKE_CLI_RESOURCE_SENTINEL_42"


@pytest.mark.parametrize("flags", [(), ("--json",)])
def test_noninteractive_vault_set_refuses_before_network_or_file_write(tmp_path, flags):
    environment = os.environ | {"LW_URL": "http://127.0.0.1:1", "SPATIAL_STATE_ROOT": str(tmp_path / "state")}
    process = subprocess.run(
        [str(LW), "vault", "set", "llm/fake", *flags],
        cwd=tmp_path,
        env=environment,
        input=SENTINEL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 1
    assert "interactive terminal" in process.stdout + process.stderr
    assert SENTINEL not in process.stdout + process.stderr
    assert not (tmp_path / "state").exists()


def test_vault_value_argument_is_rejected_without_echo(tmp_path):
    process = subprocess.run(
        [str(LW), "vault", "set", "llm/fake", SENTINEL, "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 1
    assert SENTINEL not in process.stdout + process.stderr


async def test_installed_model_commands_mcp_query_and_names_are_value_free(lab, gateway_directory):
    http, _, environment = lab
    Vault(gateway_directory / "state").set("llm/fake", SENTINEL)

    def command(*arguments):
        process = subprocess.run(
            [str(LW), *arguments, "--json"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert SENTINEL not in process.stdout + process.stderr
        return json.loads(process.stdout)

    assert command("vault", "ls") == ["llm/fake"]
    model = command(
        "model", "add", "fake-provider/fake-model", "--thinking", "high", "--credential", "llm/fake"
    )
    assert model["credentialName"] == "llm/fake" and model["latencyP50Ms"] is None
    assert command("model", "default", "fake-provider/fake-model") == {"defaultRef": model["ref"]}
    expected = command("model", "ls", "--query", "fake-provider")
    assert expected[0]["isDefault"] is True
    assert http.get("/api/v1/models?q=fake-provider").json()["models"] == expected
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    async with Client(transport, mode="legacy") as mcp:
        names = {tool.name for tool in (await mcp.list_tools()).tools}
        assert "model_ls" in names
        assert all("vault" not in name for name in names)
        assert await result(mcp, "model_ls", query="fake-provider") == expected
        assert SENTINEL not in mcp.session.instructions
        assert SENTINEL not in json.dumps(await result(mcp, "model_ls"))
    log = (gateway_directory / "gateway.log").read_text(encoding="utf-8")
    assert SENTINEL not in log


async def test_model_mcp_query_does_not_require_an_active_physics_session(gateway_url):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment["LW_URL"] = gateway_url
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        sessions = http.get("/api/v1/health").json()["sessions"]
        assert not any(item["state"] in ("RUNNING", "PAUSED", "LOADING") for item in sessions)
        expected = http.get("/api/v1/models").json()["models"]
    async with Client(
        StdioServerParameters(command=str(LW), args=["mcp"], env=environment), mode="legacy"
    ) as mcp:
        assert await result(mcp, "model_ls") == expected
