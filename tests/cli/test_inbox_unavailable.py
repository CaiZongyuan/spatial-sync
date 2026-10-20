"""A genuinely unavailable inbox preserves CLI/MCP errors and truthful instructions."""

import json
import os
import subprocess

import httpx
import pytest
from cli_support import LW, ROOT
from mcp import Client, StdioServerParameters


@pytest.fixture(scope="module")
def gateway_directory(tmp_path_factory):
    directory = tmp_path_factory.mktemp("unavailable-inbox-gateway")
    (directory / "state").mkdir()
    (directory / "state/inbox.jsonl").write_text("{interrupted identity", encoding="utf-8")
    return directory


async def test_real_unavailable_http_is_never_reported_as_zero_active_alerts(gateway_url):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert http.get("/api/v1/health").status_code == 200
        unavailable = http.get("/api/v1/inbox")
        assert unavailable.status_code == 503
    response = subprocess.run([str(LW), "inbox", "--json"], cwd=ROOT, env=environment,
                              capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert response.returncode == 1
    assert json.loads(response.stdout)["error"]["detail"]["code"] == "inbox_unavailable"
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    async with Client(transport, mode="legacy") as mcp:
        assert "Active alerts: unavailable" in mcp.session.instructions
        assert "Active alerts: 0." not in mcp.session.instructions
        result = await mcp.call_tool("inbox_poll", {})
        assert result.is_error and "inbox_unavailable" in result.content[0].text
