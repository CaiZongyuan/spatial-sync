"""Installed CLI and official MCP read the same real robot catalog over HTTP."""

import httpx
from cli_support import LW, result
from mcp import Client, StdioServerParameters
from test_training_commands import environment, invoke


async def test_installed_robot_reads_match_official_mcp_and_actual_http_without_source(gateway_url):
    env = environment(gateway_url)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=env)
    async with Client(transport, mode="legacy") as mcp:
        tools = {tool.name for tool in (await mcp.list_tools()).tools}
        assert {"robot_list", "robot_show", "robot_model_list", "robot_model_show",
                "controller_show"} <= tools
        robots = invoke(env, "robot", "ls")
        assert robots == await result(mcp, "robot_list")
        simulated = invoke(env, "robot", "show", "fr3-sim")
        assert simulated == await result(mcp, "robot_show", id="fr3-sim")
        assert simulated["bindings"][0]["program"] == "robot_arm.newton"
        models = invoke(env, "robot-model", "ls")
        assert models == await result(mcp, "robot_model_list")
        for model in models:
            assert invoke(env, "robot-model", "show", model["id"]) == await result(
                mcp, "robot_model_show", id=model["id"])
        controller = invoke(env, "controller", "show", "fr3-arm")
        assert controller == await result(mcp, "controller_show", id="fr3-arm")
        assert controller["current"] is None and controller["versions"] == []
        assert invoke(env, "robot", "show", "missing", code=1)["error"]["statusCode"] == 404
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert robots == http.get("/api/v1/embodiments").json()["embodiments"]
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
        assert http.get("/api/v1/evaluations").json()["evaluations"] == []


async def test_installed_registration_uses_http_and_generic_mcp(gateway_url, tmp_path):
    import yaml

    from packages.library.common import BUILTIN_ROOT

    env = environment(gateway_url)
    declaration = {"type": "u-cli-cart/v1", "title": "CLI cart", "actions": {"roll": {"longRunning": True}}}
    path = tmp_path / "registration.yaml"

    def publish(command, document):
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        return invoke(env, command, "register", str(path))

    registered = publish("entity-type", declaration)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=env)
    async with Client(transport, mode="legacy") as mcp:
        assert registered == await result(mcp, "entity_type_show", id=declaration["type"])
        assert registered in invoke(env, "entity-type", "ls")
        assert {"roll", "stop"} <= registered["actions"].keys()
        assert registered["type"] == declaration["type"]
        controller = {"id": "cli-controller", "title": "CLI Controller", "embodiment": "cli-robot"}
        robot = {"id": "cli-robot", "title": "CLI Robot", "asset": "robot/fr3@1", "kind": "sim",
                 "controller": controller["id"], "bindings": []}
        assert publish("robot", robot) == await result(mcp, "robot_show", id=robot["id"])
        assert publish("controller", controller) == await result(mcp, "controller_show", id=controller["id"])
        model = yaml.safe_load((BUILTIN_ROOT / "robot-models/fr3-taught-motion.yaml").read_text())
        model |= {"id": "cli-model", "embodiments": [robot["id"]]}
        assert publish("robot-model", model) == await result(mcp, "robot_model_show", id=model["id"])
