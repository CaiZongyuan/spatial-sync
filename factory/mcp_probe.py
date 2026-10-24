"""MCP-side probe: official client, safe mode and telemetry environment."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TELEMETRY_CHECK = """
import json, sys, types
try:
    import blender_mcp.config  # noqa: F401
    packaging_gap = False
except ModuleNotFoundError:
    packaging_gap = True
    class Config:
        enabled = True
        supabase_url = "https://invalid.example"
        supabase_key = ""
    module = types.ModuleType("blender_mcp.config")
    module.telemetry_config = Config()
    sys.modules["blender_mcp.config"] = module

from blender_mcp.telemetry import get_telemetry
print(json.dumps({"disabled": get_telemetry().config.enabled is False, "packagingGap": packaging_gap}))
"""


async def probe(server):
    environment = dict(os.environ) | {
        "BLENDER_MCP_SAFE_MODE": "1",
        "DISABLE_TELEMETRY": "true",
        "PYTHONUNBUFFERED": "1",
    }
    parameters = StdioServerParameters(command=str(server), args=[], env=environment)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [tool.name for tool in listed.tools]
            rejected = await session.call_tool(
                "execute_blender_code",
                {"code": "import os\nprint(os.getcwd())", "user_prompt": "SF0 safe-mode probe"},
            )
            allowed = await session.call_tool(
                "execute_blender_code",
                {"code": "import bpy\nprint(bpy.app.version_string)", "user_prompt": "SF0 safe-mode probe"},
            )
    rejected_text = rejected.content[0].text if rejected.content else ""
    allowed_text = allowed.content[0].text if allowed.content else ""
    reached = "Rejected by safe mode" not in allowed_text and (
        "Could not connect to Blender" in allowed_text or "Code executed successfully" in allowed_text
    )
    telemetry = subprocess.run(
        [sys.executable, "-c", TELEMETRY_CHECK],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        telemetry_result = json.loads(telemetry.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        telemetry_result = {
            "disabled": False,
            "packagingGap": False,
            "error": telemetry.stdout + telemetry.stderr,
        }
    return {
        "server": str(server),
        "serverVersion": version("mcp-for-blender"),
        "tools": names,
        "safeModeRejected": "Rejected by safe mode" in rejected_text,
        "allowedReachedConnection": reached,
        "rejectedMessage": rejected_text,
        "allowedMessage": allowed_text,
        "telemetryDisabled": bool(telemetry_result.get("disabled")),
        "telemetryPackagingGap": bool(telemetry_result.get("packagingGap")),
        "telemetryOutput": json.dumps(telemetry_result)[-2000:],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--out", required=True)
    options = parser.parse_args()
    result = asyncio.run(probe(options.server))
    Path(options.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not (
        "execute_blender_code" in result["tools"]
        and result["safeModeRejected"]
        and result["allowedReachedConnection"]
        and result["telemetryDisabled"]
    ):
        raise SystemExit("MCP probe configuration check failed")


if __name__ == "__main__":
    main()
