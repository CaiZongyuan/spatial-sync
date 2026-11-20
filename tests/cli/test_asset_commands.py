"""Installed CLI and official MCP upload the same bytes through generic library tools."""

import asyncio
import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, TOOLS, result
from mcp import Client, StdioServerParameters


async def test_mcp_asset_calibration_pins_existing_version(gateway_url, tmp_path):
    directory = tmp_path / "robot"
    directory.mkdir()
    (directory / "robot.json").write_text(json.dumps({
        "id": "robot/mcp-calibration", "title": "MCP calibrated robot", "entry": "robot.urdf",
        "license": "LICENSE", "licenseId": "CC0-1.0",
    }))
    (directory / "LICENSE").write_text("CC0-1.0 test fixture")
    (directory / "robot.urdf").write_text(
        '<robot name="probe"><link name="base"><collision><geometry><box size=".2 .2 .2"/>'
        '</geometry></collision></link><link name="tip"><collision><geometry>'
        '<sphere radius=".05"/></geometry></collision></link>'
        '<joint name="hinge" type="revolute"><parent link="base"/><child link="tip"/>'
        '<origin xyz=".4 0 0"/><axis xyz="0 0 1"/><limit lower="-1" upper="1" effort="3"/>'
        '</joint></robot>'
    )
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"friction": .7, "joints": {"hinge": {"zeroOffset": .2}}}))
    environment = os.environ | {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}
    server = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    async with Client(server, mode="legacy") as mcp:
        async def call(name, arguments):
            response = await asyncio.wait_for(mcp.call_tool(name, arguments), 120)
            assert not response.is_error, response.content
            return response.structured_content

        first = await call("library_import", {"directory": str(directory)})
        second = await call("library_calibrate", {"id": first["assetVersion"], "recipe": str(recipe)})
        assert second["assetVersion"] == "robot/mcp-calibration@2"
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        original = http.get("/api/v1/library/assets/robot/mcp-calibration/1").json()
        revised = http.get("/api/v1/library/assets/robot/mcp-calibration/2").json()
        assert original["contentHash"] == first["contentHash"]
        provenance = http.get(revised["files"]["provenance.json"]).json()
        assert provenance["calibratedFrom"] == {
            "id": first["assetVersion"], "contentHash": first["contentHash"],
        }


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
    assert inspected["referenceInspection"] == {
        "complete": True, "reason": None, "uninspectedScenes": [],
    }
    assert inspected["referencingScenes"] == []
    assert cli("asset", "ls", "--kind", "device", "--gaps", "-q", "Test centrifuge") == [
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
                "referenceInspection",
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
        assert await result(mcp, "library_describe", id=first["assetVersion"], view="full") == inspected
        assert await result(
            mcp, "library_search", resource="assets", kind="device", query="centrifuge", gaps=True
        ) == cli("asset", "ls", "--kind", "device", "-q", "centrifuge", "--gaps")
        assert await result(mcp, "library_search") == cli("scene", "ls")
        checked = cli("asset", "validate", first["assetVersion"], code=3)
        assert (
            checked["schema"] == "lw.validation/1" and checked["subject"] == "asset:" + first["assetVersion"]
        )
        rechecked = await result(mcp, "library_validate", id=first["assetVersion"])
        assert rechecked["contentHash"] == checked["contentHash"] == first["contentHash"]
        assert rechecked["reportId"] != checked["reportId"]
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


async def test_scene_cli_and_generic_mcp_import_and_revalidation_share_exact_immutable_versions(
    gateway_url, tmp_path
):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}
    package = tmp_path / "client-scene"
    package.mkdir()
    (package / "scene.yaml").write_text(
        "name: command-structure-fixture\ntitle: Command structure fixture\n"
        "sources: [newton]\nstage: stage.usda\n",
        encoding="utf-8",
    )
    (package / "stage.usda").write_text(
        '#usda 1.0\n(metersPerUnit = 1\n upAxis = "Z")\ndef Xform "World" {\n def Xform "Lab" {}\n}\n',
        encoding="utf-8",
    )
    (package / "devices.yaml").write_text(
        "scene: command-structure-fixture\nentities: []\n", encoding="utf-8"
    )
    (package / "locations.yaml").write_text("locations: {}\n", encoding="utf-8")

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

    first = cli("scene", "import", str(package))
    assert first["sceneVersion"] == "command-structure-fixture@1"
    checked = cli("scene", "validate", first["sceneVersion"])
    assert checked["contentHash"] == first["contentHash"] and checked["launchable"]
    async with Client(
        StdioServerParameters(command=str(LW), args=["mcp"], env=environment), mode="legacy"
    ) as mcp:
        repeated = await result(mcp, "library_import", directory=str(package))
        assert repeated["deduplicated"] and repeated["sceneVersion"] == first["sceneVersion"]
        rechecked = await result(mcp, "library_validate", id=first["sceneVersion"])
        assert rechecked["contentHash"] == checked["contentHash"]
        assert rechecked["reportId"] != checked["reportId"]
        assert rechecked["checks"] == checked["checks"]
    stage = package / "stage.usda"
    stage.write_text(
        stage.read_text(encoding="utf-8").replace('upAxis = "Z"', 'upAxis = "Y"'), encoding="utf-8"
    )
    second = cli("scene", "import", str(package))
    assert second["sceneVersion"] == "command-structure-fixture@2"
    failure = cli("scene", "validate", second["sceneVersion"], code=3)
    assert any(check["id"] == "S2" and check["status"] == "fail" for check in failure["checks"])
    assert cli("scene", "show", first["sceneVersion"])["contentHash"] == first["contentHash"]
