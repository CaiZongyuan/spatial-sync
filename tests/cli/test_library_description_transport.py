"""Bounded discovery through real HTTP, installed lw and official stdio MCP."""

import hashlib
import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, TOOLS
from mcp import Client, StdioServerParameters
from psutil import Process

from packages.library.description_views import canonical_json
from packages.library.reports import ReportStore


async def test_bounded_discovery_shared_defaults_lossless_pages_and_legacy_full(
    gateway_url, gateway_directory,
):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}

    def cli(*arguments):
        process = subprocess.run([str(LW), *arguments, "--json"], env=environment, cwd=ROOT,
                                 capture_output=True, timeout=15)
        assert process.returncode == 0, process.stderr + process.stdout
        return json.loads(process.stdout), len(process.stdout)

    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    measurements = []
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=20) as http:
        assert http.get("/api/v1/health").json()["sessions"] == []
        async with Client(transport, mode="legacy") as mcp:
            owned_processes = [{"pid": process.pid, "createdAt": process.create_time(),
                                "command": process.cmdline(), "cwd": process.cwd()}
                               for process in Process().children(recursive=True)]
            tools = (await mcp.list_tools()).tools
            assert {tool.name for tool in tools} == TOOLS
            schema = next(tool for tool in tools if tool.name == "library_describe").input_schema
            assert schema["properties"]["view"]["default"] == "summary"
            assert schema["properties"]["view"]["enum"] == ["summary", "page", "full"]

            async def bounded(**arguments):
                result = await mcp.call_tool("library_describe", arguments)
                assert not result.is_error, result.content
                assert len(result.model_dump_json(by_alias=True).encode("utf-8")) <= 16384
                measurements.append({"id": arguments["id"], "view": arguments.get("view", "summary"),
                                     "wireBytes": len(result.model_dump_json(by_alias=True).encode("utf-8"))})
                value = result.structured_content
                if set(value) == {"result"}:
                    value = value["result"]
                assert len(json.dumps(value, ensure_ascii=False).encode("utf-8")) <= 4096
                return value

            for resource, selector, route in [
                ("scene", "robot-and-tubes@2", "/api/v1/library/scenes/robot-and-tubes/2"),
                ("asset", "robot/fr3@1", "/api/v1/library/assets/robot/fr3/1"),
            ]:
                full = http.get(route).json()
                assert http.get(route).json() == full  # real repeated whole-document digest stability
                assert http.get(route, params={"view": "full"}).json() == full
                assert cli(resource, "show", selector)[0] == full
                legacy = await mcp.call_tool("library_describe", {"id": selector, "view": "full"})
                assert not legacy.is_error and legacy.structured_content == full
                summary = http.get(route, params={"view": "summary"}).json()
                assert summary["documentDigest"] == hashlib.sha256(canonical_json(full).encode()).hexdigest()
                assert await bounded(id=selector) == summary
                actual_cli, size = cli("library", "describe", selector)
                assert size <= 4096 and actual_cli == summary
                measurements.append({"id": selector, "summaryCliBytes": size,
                                     "fullCanonicalBytes": len(canonical_json(full).encode("utf-8")),
                                     "stableWholeDigest": True})
                for section in (None, "types"):
                    offset, pieces = 0, []
                    while True:
                        page = await bounded(id=selector, view="page", section=section, offset=offset,
                                             documentDigest=summary["documentDigest"])
                        pieces.append(page["content"])
                        if offset == 0 and section is None:
                            params = {"view": "page", "documentDigest": summary["documentDigest"]}
                            response = http.get(route, params=params)
                            assert len(response.content) <= 4096 and response.json() == page
                            assert cli("library", "describe", selector, "--view", "page",
                                       "--document-digest", summary["documentDigest"])[0] == page
                        if page["nextOffset"] is None:
                            break
                        offset = page["nextOffset"]
                    expected = full if section is None else full[section]
                    assert "".join(pieces) == canonical_json(expected)
                    assert json.loads("".join(pieces)) == expected
                stale = http.get(route, params={"view": "page", "documentDigest": "0" * 64})
                assert stale.status_code == 409
                assert stale.json()["detail"]["code"] == "description_changed"
                alias = route.rsplit("/", 1)[0]
                rejected = http.get(alias, params={"view": "page",
                                                   "documentDigest": summary["documentDigest"]})
                assert rejected.status_code == 400
                # Append the same recorded report as test metadata, without running a validator.
                ReportStore(gateway_directory / "state").save(
                    full["validation"], full["contentHash"])
                changed = http.get(route, params={"view": "page",
                                                   "documentDigest": summary["documentDigest"]})
                assert changed.status_code == 409
                refreshed = await bounded(id=selector)
                assert refreshed["documentDigest"] != summary["documentDigest"]
                assert refreshed["contentHash"] == summary["contentHash"]
        assert http.get("/api/v1/health").json()["sessions"] == []
    (gateway_directory / "description-receipt.json").write_text(
        json.dumps({"url": gateway_url, "ownedProcesses": owned_processes,
                    "sessions": [], "measurements": measurements}, indent=2),
        encoding="utf-8")
