"""Installed clients snapshot inert local program bytes through the shared HTTP resources."""

import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, Implementation, StdioServerParameters

from tests.fixtures.script_program import SOURCE, program_directory


async def test_installed_program_cli_and_mcp_preserve_source_and_immutable_identity(gateway_url, tmp_path):
    env = os.environ | {"LW_URL": gateway_url, "LW_SESSION": "", "PYTHONIOENCODING": "utf-8"}
    directory, manifest = program_directory(tmp_path)

    def invoke(*arguments, code=0):
        process = subprocess.run(
            [str(LW), *map(str, arguments), "--json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert process.returncode == code, process.stdout + process.stderr
        return json.loads(process.stdout)

    assert invoke("program", "ls") == []
    inspected = invoke("program", "inspect", directory)
    assert inspected["metadata"] == manifest
    pushed = invoke("program", "push", directory)
    assert pushed["contentHash"] == inspected["contentHash"]
    assert pushed["id"] == "program/robot_arm.script.probe@1" and pushed["plans"] == []
    assert invoke("program", "push", directory)["idempotent"]
    assert invoke("program", "show", pushed["id"])["contentHash"] == pushed["contentHash"]
    refused = invoke("program", "promote", pushed["id"], "--scene", "robot-and-tubes@2", code=1)
    assert refused["error"]["statusCode"] == 409
    assert refused["error"]["detail"]["code"] == "candidate_not_accepted"
    assert invoke("program", "source", pushed["id"], "controller.py")["content"].encode() == SOURCE
    exported = tmp_path / "frozen.tar"
    assert invoke("program", "export", pushed["id"], exported)["output"] == str(exported)
    invoke("program", "export", pushed["id"], exported, code=1)  # Preserve an existing client file.
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert exported.read_bytes() == http.get("/api/v1/programs/probe/1/archive").content
    (directory / "controller.py").write_bytes(SOURCE + b"\n# Retain dirty source bytes.\r\n")
    changed = invoke("program", "push", directory)
    assert changed["id"] == "program/robot_arm.script.probe@2"
    assert changed["contentHash"] != pushed["contentHash"]

    async with Client(
        StdioServerParameters(command=str(LW), args=["mcp"], env=env),
        mode="legacy",
        client_info=Implementation(name="program-author", version="1"),
    ) as mcp:
        names = {tool.name for tool in (await mcp.list_tools()).tools}
        assert {
            "program_inspect", "program_push", "program_list", "program_show", "program_source",
            "program_export", "program_promote", "controller_promotion_show",
        } <= names
        assert not {"controller_promotion_review", "controller_promotion_approve"} & names
        denied = await mcp.call_tool(
            "program_promote", {"id": pushed["id"], "scene": "robot-and-tubes@2"}
        )
        assert denied.is_error and "candidate_not_accepted" in denied.content[0].text
        assert len(await result(mcp, "program_list")) == 2
        checked = await result(mcp, "program_inspect", directory=str(directory))
        assert checked["contentHash"] == changed["contentHash"]
        assert (await result(mcp, "program_push", directory=str(directory)))["idempotent"]
        assert (await result(mcp, "program_show", id=pushed["id"]))["contentHash"] == pushed["contentHash"]
        source = await result(mcp, "program_source", id=changed["id"], filename="controller.py")
        assert source["content"].encode() == (directory / "controller.py").read_bytes()
        mcp_export = tmp_path / "mcp-frozen.tar"
        await result(mcp, "program_export", id=pushed["id"], output=str(mcp_export))
        assert mcp_export.read_bytes() == exported.read_bytes()
    (directory / "controller.py").write_bytes(b"import newton\n" + SOURCE)
    refused = invoke("program", "push", directory, code=1)
    assert refused["error"]["detail"]["code"] == "program_invalid"
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
        assert http.get("/api/v1/jobs").json()["jobs"] == []
