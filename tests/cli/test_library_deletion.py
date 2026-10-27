"""Installed rm commands and official MCP remove and restore without human Approval."""

import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters


async def test_installed_rm_aliases_and_generic_mcp_keep_the_human_boundary(gateway_url, tmp_path):
    environment = os.environ | {"LW_URL": gateway_url, "LW_SESSION": "", "PYTHONIOENCODING": "utf-8"}

    def cli(*args, success=True):
        completed = subprocess.run(
            [str(LW), *args, "--json"], env=environment, cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", timeout=20,
        )
        assert (completed.returncode == 0) is success, (completed.stdout, completed.stderr)
        return json.loads(completed.stdout)

    asset = tmp_path / "asset"
    asset.mkdir()
    asset.joinpath("asset.yaml").write_text(
        "id: labware/cli-deletion\nkind: labware\ntitle: CLI deletion fixture\nstage: asset.usda\n",
        encoding="utf-8",
    )
    asset.joinpath("asset.usda").write_text(
        '#usda 1.0\n(defaultPrim = "Asset"\nupAxis = "Z"\nmetersPerUnit = 1)\ndef Xform "Asset" {}\n',
        encoding="utf-8",
    )
    scene = tmp_path / "scene"
    scene.mkdir()
    scene.joinpath("scene.yaml").write_text(
        "name: cli-deletion\ntitle: CLI deletion scene\nsources: [newton]\nstage: stage.usda\n",
        encoding="utf-8",
    )
    scene.joinpath("stage.usda").write_text(
        '#usda 1.0\n(upAxis = "Z"\nmetersPerUnit = 1)\ndef Xform "World" {\n'
        ' def Xform "Lab" {}\n}\n',
        encoding="utf-8",
    )
    scene.joinpath("devices.yaml").write_text("scene: cli-deletion\nentities: []\n", encoding="utf-8")
    scene.joinpath("locations.yaml").write_text("locations: {}\n", encoding="utf-8")
    assert cli("asset", "import", str(asset))["assetVersion"] == "labware/cli-deletion@1"
    imported = cli("scene", "import", str(scene))
    assert imported["sceneVersion"] == "cli-deletion@1"
    dependency_check = next(check for check in imported["validation"]["checks"] if check["id"] == "S7")
    assert dependency_check["status"] == "pass"
    for identifier in ("labware/cli-deletion", "labware/cli-deletion@0", "cli-deletion@01"):
        assert "exact" in cli("asset", "rm", identifier, success=False)["error"]["message"]
    requested = cli("asset", "rm", "labware/cli-deletion@1", "--request-id", "cli-delete")
    other = cli("scene", "rm", "cli-deletion@1")
    assert requested["status"] == other["status"] == "committed"
    assert cli("asset", "rm", "labware/cli-deletion@1", "--request-id", "cli-delete") == requested
    rejected = cli("approve", requested["id"], success=False)
    assert rejected["error"]["detail"]["code"] == "approval_requires_human"
    async with Client(
        StdioServerParameters(command=str(LW), args=["mcp"], env=environment), mode="legacy",
    ) as mcp:
        tools = (await mcp.list_tools()).tools
        assert any(tool.name == "library_remove" for tool in tools)
        assert not any("approv" in tool.name for tool in tools)
        assert await result(mcp, "library_deletion_show", id=requested["id"]) == requested
        restored = await result(mcp, "library_deletion_restore", id=requested["id"])
        assert restored["status"] == "restored"
        removed = await result(mcp, "library_remove", id="labware/cli-deletion@1")
        assert removed["status"] == "committed"
        forbidden = await mcp.call_tool("approve_invocation", {"invocation_id": requested["id"]})
        assert forbidden.is_error and "approval_requires_human" in forbidden.content[0].text
    assert cli("deletion", "restore", other["id"])["status"] == "restored"
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert http.get("/api/v1/approvals").json()["pendingCount"] == 0
        assert http.get("/api/v1/library/assets/labware/cli-deletion/1").status_code == 404
        assert http.get("/api/v1/library/scenes/cli-deletion/1").status_code == 200
