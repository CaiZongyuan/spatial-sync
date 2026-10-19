"""Generic MCP tools backed by the same HTTP client as ``lw``."""

import json
import os
from collections import defaultdict
from inspect import Parameter, Signature
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from .client import GatewayClient, GatewayError, condition_hint
from .commands import COMMANDS


def catalog_instructions(catalog):
    groups = defaultdict(list)
    locations = set()
    units = set()
    for key, binding in catalog["entities"].items():
        groups[binding["type"]].append(key)
        config = binding.get("config", {}).get("locations", {})
        if isinstance(config, dict):
            locations.update(config.get("locations", {}))
    for definition in catalog["types"].values():
        units.update(prop["unit"] for prop in definition["properties"].values() if prop.get("unit"))
    entities = []
    for type_id, keys in sorted(groups.items()):
        keys.sort()
        prefix = os.path.commonprefix(keys) if len(keys) > 1 else ""
        names = (
            prefix + "{" + ",".join(key[len(prefix) :] for key in keys) + "}" if prefix else ",".join(keys)
        )
        entities.append(type_id + ": " + names)
    advice = (
        "Laboratory devices and their current state/actions are described by the Catalog. "
        "Describe an entity before acting. On rejection, read error.details and hint, "
        "then correct the cause. "
        "Wait for completion before dependent actions. Approval requires a human operator. "
        "Coordinates: right-handed, Z up, metres; quaternion [x,y,z,w]. "
    )
    # Keep the conventions and workflow intact even when a scene has thousands of entities.
    catalog_text = (
        "Units: "
        + ",".join(sorted(units))
        + ". Locations: "
        + ",".join(sorted(locations))
        + ". Entities: "
        + "; ".join(entities)
    )
    return advice + catalog_text[: 1500 - len(advice)]


def actor(context):
    params = context.request_context.session.client_params
    name = params.client_info.name if params is not None else "unknown-mcp-client"
    return "agent:" + name


def with_hint(invocation):
    hint = condition_hint(invocation)
    return invocation | {"hint": hint} if hint else invocation


async def checked(request):
    try:
        return await request
    except GatewayError as exc:
        raise ToolError(json.dumps(exc.document(), ensure_ascii=False)) from exc


def command_tool(command, client):
    async def invoke(**arguments):
        peer = actor(arguments.pop("ctx")) if command.actor else None
        result = await checked(command.invoke(client, arguments, actor=peer))
        return with_hint(result) if isinstance(result, dict) and "invocationId" in result else result

    # The official SDK derives validation and wire schemas from this signature.
    invoke.__name__ = command.mcp_name
    parameters = [
        Parameter(
            argument.name,
            Parameter.KEYWORD_ONLY,
            annotation=argument.annotation,
            default=argument.default,
        )
        for argument in command.arguments
    ]
    if command.actor:
        parameters.append(Parameter("ctx", Parameter.KEYWORD_ONLY, annotation=Context))
    invoke.__signature__ = Signature(parameters, return_annotation=command.output_type)
    invoke.__annotations__ = {parameter.name: parameter.annotation for parameter in parameters}
    return invoke


async def create_server(client: GatewayClient):
    human_only = {command.mcp_name for command in COMMANDS if command.human_only}

    async def reject_human_decisions(context, call_next):
        if context.method == "tools/call" and (context.params or {}).get("name") in human_only:
            return CallToolResult(
                content=[
                    TextContent(type="text", text=json.dumps({"error": {"code": "approval_requires_human"}}))
                ],
                is_error=True,
            )
        return await call_next(context)

    catalog = await client.catalog_if_active()
    instructions = catalog_instructions(catalog) if catalog is not None else (
        "Use library_search and library_describe to inspect assets and scenes; library_import uploads a "
        "directory from this MCP client's filesystem. Use scene_launch to start an inspected scene. "
        "Describe entities before acting. Approval requires a human operator."
    )
    server = MCPServer("Laboratory", instructions=instructions, middleware=[reject_human_decisions])

    for command in COMMANDS:
        if command.mcp_name and not command.human_only:
            server.add_tool(command_tool(command, client), name=command.mcp_name, description=command.help)

    @server.tool()
    async def get_state(keys: list[str]) -> dict[str, Any]:
        """Read the current properties and metadata of the requested entities."""
        return await checked(client.get_state(keys))

    @server.tool()
    async def invoke_action(
        ctx: Context,
        key: str,
        action: str,
        params: dict[str, Any] | None = None,
        wait: bool = True,
        timeoutSeconds: float = 120,
        planId: str | None = None,
        stepId: int | None = None,
        planRevision: int | None = None,
    ) -> dict[str, Any]:
        """Invoke any Catalog action. Read error.details/hint after a rejection; wait by default."""
        return with_hint(
            await checked(
                client.invoke_action(
                    key,
                    action,
                    params,
                    wait=wait,
                    timeout=timeoutSeconds,
                    actor=actor(ctx),
                    planId=planId, stepId=stepId, planRevision=planRevision,
                )
            )
        )

    @server.tool()
    async def get_invocation(
        invocationId: str,
        wait: bool = False,
        timeoutSeconds: float = 120,
    ) -> dict[str, Any]:
        """Read a call, optionally waiting until it finishes or the wait expires."""
        return with_hint(
            await checked(client.get_invocation(invocationId, wait=wait, timeout=timeoutSeconds))
        )

    @server.tool()
    async def wait_until(
        ctx: Context,
        key: str,
        property: str,
        op: str,
        value: Any,
        timeoutSeconds: float = 120,
        planId: str | None = None,
        stepId: int | None = None,
        planRevision: int | None = None,
    ) -> dict[str, Any]:
        """Wait for a Catalog property condition; a timed-out wait returns satisfied=false."""
        return await checked(client.wait_until(
            key, property, op, value, timeout=timeoutSeconds, actor=actor(ctx),
            planId=planId, stepId=stepId, planRevision=planRevision,
        ))

    @server.tool()
    async def check_condition(
        ctx: Context, key: str, property: str, op: str, value: Any,
        planId: str | None = None, stepId: int | None = None, planRevision: int | None = None,
    ) -> dict[str, Any]:
        """Assert a property condition, optionally completing an operation-plan step."""
        return await checked(client.check_condition(key, property, op, value, actor=actor(ctx),
                                                    planId=planId, stepId=stepId, planRevision=planRevision))

    @server.tool()
    async def session_control(
        ctx: Context,
        action: Literal["pause", "resume", "reset", "step"],
        count: int = 1,
    ) -> dict[str, Any]:
        """Pause, resume, reset or advance a paused simulation by count physics steps."""
        result = await checked(client.session_control(action, count=count, actor=actor(ctx)))
        return result["state"] | {"ack": result["ack"]}

    return server


async def serve(client):
    await (await create_server(client)).run_stdio_async()
