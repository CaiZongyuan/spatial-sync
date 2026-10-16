"""Eight generic MCP tools backed by the same HTTP client as ``lw``."""

import json
import os
from collections import defaultdict
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from .client import GatewayClient, GatewayError, condition_hint


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


async def create_server(client: GatewayClient):
    server = MCPServer("Laboratory", instructions=catalog_instructions(await client.catalog()))

    @server.tool()
    async def list_entities(type: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        """List entities, optionally filtered by Catalog type or text query."""
        return await checked(client.list_entities(type, query))

    @server.tool()
    async def describe_entity(key: str) -> dict[str, Any]:
        """Read definition, units, action schemas/conditions/risks, state and active calls before acting."""
        return await checked(client.describe_entity(key))

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
    async def cancel_invocation(ctx: Context, invocationId: str) -> dict[str, Any]:
        """Request cancellation of an active invocation."""
        return with_hint(await checked(client.cancel_invocation(invocationId, actor=actor(ctx))))

    @server.tool()
    async def wait_until(
        key: str,
        property: str,
        op: str,
        value: Any,
        timeoutSeconds: float = 120,
    ) -> dict[str, Any]:
        """Wait for a Catalog property condition; a timed-out wait returns satisfied=false."""
        return await checked(client.wait_until(key, property, op, value, timeout=timeoutSeconds))

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
