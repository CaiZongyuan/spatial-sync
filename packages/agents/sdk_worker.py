"""Trusted clean-environment entrypoint. No candidate code or startup command is run."""

import asyncio
import dataclasses
import importlib.metadata
import json
import os
import sys
from pathlib import Path

SDK_VERSION = "0.2.174"


def redact(value):
    if isinstance(value, str):
        for name in ("LW_TOKEN", "ANTHROPIC_API_KEY"):
            secret = os.environ.get(name)
            if secret:
                value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value]
    return value


def emit(kind, data):
    print(json.dumps({"kind": kind, "data": redact(data)}, ensure_ascii=False, allow_nan=False), flush=True)


def options(configuration):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
    from lw.scoped import TARGET_TOOLS

    if importlib.metadata.version("claude-agent-sdk") != SDK_VERSION:
        raise RuntimeError("Unsupported Agent SDK version")
    allowed = {"mcp__lab__" + name for name in TARGET_TOOLS}

    async def before_tool(value, tool_id, _context):
        admitted = value.get("tool_name") in allowed
        emit(
            "tool.before",
            {
                "toolUseId": tool_id,
                "allowed": admitted,
                "tool": value.get("tool_name"),
                "input": value.get("tool_input"),
            },
        )
        if not admitted:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Tool is outside the approved profile",
                }
            }
        return {}

    async def after_tool(value, tool_id, _context):
        emit(
            "tool.after",
            {"toolUseId": tool_id, "tool": value.get("tool_name"), "response": value.get("tool_response")},
        )
        return {}

    mcp = {}
    if configuration["platformMcp"]:
        mcp["lab"] = {
            "command": sys.executable,
            "args": ["-I", "-m", "lw", "mcp"],
            "env": {key: os.environ[key] for key in ("LW_URL", "LW_SESSION", "LW_AGENT_RUN", "LW_TOKEN")},
        }
    thinking = configuration["thinking"]
    return ClaudeAgentOptions(
        tools=[],
        allowed_tools=sorted(allowed) if mcp else [],
        mcp_servers=mcp,
        strict_mcp_config=True,
        setting_sources=[],
        skills=[],
        plugins=[],
        system_prompt=configuration["systemPrompt"],
        model=configuration["model"],
        thinking={"type": "disabled"} if thinking == "none" else {"type": "adaptive"},
        effort=None if thinking in ("default", "none") else thinking,
        permission_mode="dontAsk",
        cwd=str(Path.cwd()),
        max_turns=configuration["maxTurns"],
        fallback_model=None,
        extra_args={"no-session-persistence": None},
        env={"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(configuration["maxOutputTokens"])},
        hooks={
            "PreToolUse": [HookMatcher(hooks=[before_tool])],
            "PostToolUse": [HookMatcher(hooks=[after_tool])],
        },
    )


async def run(configuration):
    from claude_agent_sdk import query
    from lw.scoped import TARGET_TOOLS

    async for message in query(prompt=configuration["statement"], options=options(configuration)):
        if type(message).__name__ == "SystemMessage" and message.subtype == "init":
            expected = (
                {"mcp__lab__" + name for name in TARGET_TOOLS} if configuration["platformMcp"] else set()
            )
            servers = message.data.get("mcp_servers", [])
            if (
                set(message.data.get("tools", [])) != expected
                or {item["name"] for item in servers} != ({"lab"} if expected else set())
                or any(item.get("status") != "connected" for item in servers)
                or message.data.get("additional_directories")
            ):
                raise RuntimeError("The effective tool profile differs from the approved runtime")
        emit("message", {"type": type(message).__name__, "value": dataclasses.asdict(message)})


def main():
    try:
        configuration = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        asyncio.run(run(configuration))
    except BaseException as error:
        # SDK diagnostics may include env or provider text. Keep public failure bounded and value-free.
        emit("worker.error", {"type": type(error).__name__})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
