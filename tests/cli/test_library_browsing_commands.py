"""Installed clients share indexed scene pages while retaining legacy array results."""

import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters


async def test_scene_pages_match_http_cli_mcp_and_legacy_default(gateway_url):
    environment = os.environ | {"LW_URL": gateway_url, "LW_SESSION": "", "PYTHONIOENCODING": "utf-8"}

    def cli(*args):
        response = subprocess.run([str(LW), *args, "--json"], env=environment, cwd=ROOT,
                                  capture_output=True, text=True, encoding="utf-8", timeout=20)
        assert response.returncode == 0, response.stderr
        return json.loads(response.stdout)

    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        legacy = http.get("/api/v1/library/scenes").json()["scenes"]
        assert cli("scene", "ls") == legacy
        page = http.get("/api/v1/library/scenes?view=summary&limit=2").json()
        assert cli("scene", "ls", "--limit", "2") == page
        async with Client(StdioServerParameters(command=str(LW), args=["mcp"], env=environment),
                          mode="legacy") as mcp:
            assert await result(mcp, "library_search", resource="scenes", limit=2) == page
            following = await result(mcp, "library_search", resource="scenes", limit=2,
                                     cursor=page["pagination"]["nextCursor"])
            assert not {item["id"] for item in page["scenes"]} & {
                item["id"] for item in following["scenes"]
            }
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
