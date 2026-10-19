"""Owned process cleanup and official SDK result handling used by CLI tests."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LW = Path(sys.executable).parent / ("lw.exe" if os.name == "nt" else "lw")
TOOLS = {
    "library_search",
    "library_describe",
    "scene_launch",
    "list_entities",
    "describe_entity",
    "get_state",
    "invoke_action",
    "get_invocation",
    "cancel_invocation",
    "wait_until",
    "session_control",
}


def stop(process):
    try:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5
                )
            else:
                process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def result(client, name, **arguments):
    response = await asyncio.wait_for(client.call_tool(name, arguments), 5)
    assert not response.is_error, response.content
    document = response.structured_content
    return document["result"] if set(document) == {"result"} else document
