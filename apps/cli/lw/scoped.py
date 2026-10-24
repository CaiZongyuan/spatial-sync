"""The managed Target's fixed MCP client; authority is checked again by HTTP."""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import quote, urlsplit

from .client import GatewayClient, GatewayError

TARGET_TOOLS = frozenset(
    {
        "list_entities",
        "describe_entity",
        "get_state",
        "invoke_action",
        "get_invocation",
        "cancel_invocation",
        "wait_until",
        "check_condition",
        "session_control",
        "plan_create",
        "plan_revise",
        "plan_show",
        "plan_list",
        "alerts_list",
        "alert_ack",
        "inbox_poll",
    }
)
INBOX_KINDS = frozenset({"alert", "invocation", "plan"})
_tool = ContextVar("managed_mcp_tool", default=None)


class ScopedGatewayClient(GatewayClient):
    allowed_tools = TARGET_TOOLS

    def __init__(self, *, run_id, session, token, url, http=None):
        endpoint = urlsplit(url)
        if (
            not run_id
            or not session
            or not token
            or not token.startswith("lw-agent-")
            or endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "::1"}
            or endpoint.username
            or endpoint.password
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise GatewayError("Managed MCP requires its generated loopback Session grant")
        super().__init__(url=url, token=token, session=session, http=http)
        self._managed_actor = "agent:managed-" + run_id
        self._owned_route = "/api/v1/sessions/" + quote(session, safe="")

    @classmethod
    def from_environment(cls):
        return cls(
            run_id=os.environ["LW_AGENT_RUN"],
            session=os.getenv("LW_SESSION"),
            token=os.getenv("LW_TOKEN"),
            url=os.getenv("LW_URL", ""),
        )

    async def request(self, method, path, *, actor=None, **kwargs):
        if not path.startswith("/api/v1/") or "://" in path:
            raise GatewayError("Managed MCP cannot select another server", status_code=403)
        kwargs.pop("headers", None)
        headers = {"x-lw-tool-name": _tool.get()} if _tool.get() else {}
        return await super().request(method, path, actor=self._managed_actor, headers=headers, **kwargs)

    @contextmanager
    def tool_scope(self, name):
        reset = _tool.set(name)
        try:
            yield
        finally:
            _tool.reset(reset)

    async def route(self, key=None):
        if key is not None and key.startswith("/Real/"):
            raise GatewayError("Live devices are not granted to Target", status_code=403)
        return self._owned_route

    async def invocation_route(self, invocation_id):
        return self._owned_route

    async def catalog_if_active(self):
        return await self.catalog()

    async def plan_list(self, status=None):
        params = {"sessionId": self._session_id}
        if status is not None:
            params["status"] = status
        return (await self.request("GET", "/api/v1/plans", params=params))["plans"]

    async def inbox_poll(self, after=None, kinds=None):
        selected = INBOX_KINDS if kinds is None else frozenset(kinds.split(","))
        if not selected or not selected <= INBOX_KINDS:
            raise GatewayError("This inbox kind is not granted to Target", status_code=403)
        params = {"sessionId": self._session_id, "kinds": ",".join(sorted(selected))}
        if after is not None:
            params["after"] = after
        return await self.request("GET", "/api/v1/inbox", params=params)
