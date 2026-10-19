"""Installed CLI and official MCP upload the same bytes through generic library tools."""

import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, TOOLS, result
from mcp import Client, StdioServerParameters


async def test_asset_cli_mcp_upload_filters_description_and_export_dedup(gateway_url, tmp_path):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}
    package = tmp_path / "client-asset"
    package.mkdir()
    (package / "asset.yaml").write_text(
        "id: device/test-centrifuge\nkind: device\ntitle: Test centrifuge\nstage: asset.usda\n"
        "implements:\n  - type: centrifuge/v1\n",
        encoding="utf-8",
    )
    (package / "asset.usda").write_text('#usda 1.0\ndef Xform "Asset" {}\n', encoding="utf-8")
    # Client-side regular files sharing an inode must still upload as independent tar bytes.
    os.link(package / "asset.usda", package / "copy.usda")

    def cli(*arguments, code=0):
        invocation = subprocess.run(
            [str(LW), *arguments, "--json"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert invocation.returncode == code, (invocation.stdout, invocation.stderr)
        return json.loads(invocation.stdout)

    first = cli("asset", "import", str(package))
    assert first["assetVersion"] == "device/test-centrifuge@1"
    inspected = cli("asset", "show", first["assetVersion"])
    assert "start" in inspected["types"]["centrifuge/v1"]["actions"]
    assert inspected["validation"]["launchable"] is False
    assert cli("asset", "ls", "--kind", "device", "--gaps", "-q", "centrifuge") == [
        {
            key: value
            for key, value in inspected.items()
            if key
            not in (
                "metadata",
                "validation",
                "validationCoverage",
                "provenance",
                "referencingScenes",
                "types",
                "files",
            )
        }
    ]
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        exported = tmp_path / "exported"
        exported.mkdir()
        for name, url in inspected["files"].items():
            path = exported / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(http.get(url).content)
        exact = cli("asset", "import", str(exported))
        assert exact["deduplicated"] and exact["assetVersion"] == first["assetVersion"]
        assert exact["contentHash"] == first["contentHash"]
    async with Client(
        StdioServerParameters(command=str(LW), args=["mcp"], env=environment), mode="legacy"
    ) as mcp:
        tools = (await mcp.list_tools()).tools
        assert {tool.name for tool in tools} == TOOLS
        assert not any("approv" in tool.name for tool in tools)
        assert await result(mcp, "library_describe", id=first["assetVersion"]) == inspected
        assert await result(
            mcp, "library_search", resource="assets", kind="device", query="centrifuge", gaps=True
        ) == cli("asset", "ls", "--kind", "device", "-q", "centrifuge", "--gaps")
        assert await result(mcp, "library_search") == cli("scene", "ls")
        same = await result(mcp, "library_import", directory=str(package))
        assert same["assetVersion"] == first["assetVersion"] and same["deduplicated"]
        (package / "NOTICE").write_text("Second content version", encoding="utf-8")
        second = await result(mcp, "library_import", directory=str(package))
        assert second["assetVersion"] == "device/test-centrifuge@2" and not second["deduplicated"]
        assert cli("asset", "show", "device/test-centrifuge")["id"] == second["assetVersion"]
        (package / "validation.json").write_text('{"launchable":true}', encoding="utf-8")
        forged = cli("asset", "import", str(package), code=1)
        assert forged["error"]["detail"]["code"] == "import_invalid"


def test_asset_import_missing_local_directory_is_a_structured_cli_error(gateway_url, tmp_path):
    invocation = subprocess.run(
        [str(LW), "asset", "import", str(tmp_path / "missing"), "--json"],
        cwd=ROOT,
        env=os.environ | {"LW_URL": gateway_url},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert invocation.returncode == 1
    assert "client" in json.loads(invocation.stdout)["error"]["message"]
