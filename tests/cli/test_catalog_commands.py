"""Catalog search/show/estimate stay identical over HTTP, installed lw and MCP."""

import asyncio
import copy
import hashlib
import io
import json
import os
import subprocess
import tarfile

import httpx
from cli_support import LW, ROOT, TOOLS, result
from mcp import Client, StdioServerParameters

CATALOG_ID = "simready/box_cardboard_a01@524747d9b5e1f21b5b18b56041788c01d745f1a1"
ENTRY_ROUTE = (
    "/api/v1/library/catalog/entries/simready/box_cardboard_a01/"
    "524747d9b5e1f21b5b18b56041788c01d745f1a1"
)
UNKNOWN_ID = "simready/missing_package@" + "0" * 40


def upload_correlated_box(http):
    stage = (
        b'#usda 1.0\n(defaultPrim = "Asset"\n upAxis = "Z"\n metersPerUnit = 1)\n'
        b'def Xform "Asset" {}\n'
    )
    provenance = {
        "sourceRevision": "524747d9b5e1f21b5b18b56041788c01d745f1a1",
        "sourcePackage": "box_cardboard_a01",
        "license": {"name": "CC-BY-4.0"},
        "files": {"asset.usda": {"sha256": hashlib.sha256(stage).hexdigest(),
                                 "bytes": len(stage)}},
    }
    files = {
        "asset.yaml": (
            b"id: environment/cli-box\nkind: environment\ntitle: CLI box\n"
            b"stage: asset.usda\ndimensionsM: [0.699435, 0.521152, 0.509927]\n"
        ),
        "asset.usda": stage,
        "LICENSE.txt": b"CC-BY-4.0\n",
        "provenance.json": json.dumps(provenance).encode(),
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    response = http.post(
        "/api/v1/library/imports",
        content=output.getvalue(),
        headers={"Content-Type": "application/x-tar"},
    )
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["assetVersion"] == "environment/cli-box@1"
    return job


def client_environment(gateway_url):
    return {key: value for key, value in os.environ.items() if key != "LW_SESSION"} | {
        "LW_URL": gateway_url,
        "PYTHONIOENCODING": "utf-8",
    }


def cli(environment, *arguments, code=0):
    invocation = subprocess.run(
        [str(LW), *arguments, "--json"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert invocation.returncode == code, (arguments, invocation.stdout, invocation.stderr)
    return json.loads(invocation.stdout)


async def cli_async(environment, *arguments, code=0):
    return await asyncio.to_thread(cli, environment, *arguments, code=code)


def error_code(document):
    return document["error"]["detail"]["code"]


def mcp_error(result_document):
    text = result_document.content[0].text
    return json.loads(text[text.index("{"):])


def search_view(document):
    """Snapshot age is wall-clock derived and advances between sequential calls."""
    value = copy.deepcopy(document)
    value["snapshot"]["ageSeconds"] = None
    return value


async def test_catalog_search_show_estimate_pages_errors_and_local_shape(gateway_url):
    environment = client_environment(gateway_url)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=20) as http:
        upload_correlated_box(http)
        for installed, present in ((True, True), (False, False)):
            document = http.get(
                "/api/v1/library/search",
                params={"q": "box", "scope": "online", "installed": installed},
            ).json()
            assert (CATALOG_ID in {item["id"] for item in document["results"]}) is present
        async with Client(transport, mode="legacy") as mcp:
            assert {tool.name for tool in (await mcp.list_tools()).tools} == TOOLS

            # Bilingual queries return the same candidates through every surface;
            # online and all differ only in the echoed scope for these queries.
            searches = []
            for query, cli_scope, mcp_scope in (
                ("纸箱", "all", "online"),
                ("carton", "online", "all"),
                ("用来装实验室废弃物的容器", "all", "online"),
            ):
                expected = {
                    scope: http.get(
                        "/api/v1/library/search", params={"q": query, "scope": scope}
                    ).json()
                    for scope in ("online", "all")
                }
                searches.append((query, cli_scope, mcp_scope, expected))
            cli_searches = await asyncio.gather(*[
                cli_async(environment, "catalog", "search", "-q", query, "--scope", cli_scope)
                for query, cli_scope, _, _ in searches
            ])
            for (query, cli_scope, mcp_scope, expected), actual in zip(
                searches, cli_searches, strict=True
            ):
                assert expected["online"]["results"] == expected["all"]["results"]
                assert expected["online"]["counts"] == expected["all"]["counts"]
                for scope in ("online", "all"):
                    assert CATALOG_ID in {item["id"] for item in expected[scope]["results"]}
                assert search_view(actual) == search_view(expected[cli_scope])
                assert search_view(await result(
                    mcp, "catalog_search", query=query, scope=mcp_scope
                )) == search_view(expected[mcp_scope])
                if query == "纸箱":
                    assert expected["online"]["results"][0]["names"]["zh"] == "纸箱"

            # Explicit filters, including the local result shape retained for Web cards.
            cases = (
                ("usage", True, ("-q", "box", "--usage", "staticCollision"),
                 {"q": "box", "usage": "staticCollision"},
                 {"query": "box", "usage": "staticCollision"}),
                ("license", True, ("-q", "carton", "--license", "admitted"),
                 {"q": "carton", "licenseAdmitted": True},
                 {"query": "carton", "license": "admitted"}),
                ("installed", False, ("-q", "fr3", "--scope", "local", "--installed"),
                 {"q": "fr3", "scope": "local", "installed": True},
                 {"query": "fr3", "scope": "local", "installed": True}),
                ("notInstalled", True, ("-q", "box", "--scope", "online", "--not-installed"),
                 {"q": "box", "scope": "online", "installed": False},
                 {"query": "box", "scope": "online", "notInstalled": True}),
                ("sourceSort", True, (
                    "-q", "environment", "--scope", "online", "--provider", "simready",
                    "--sort", "sourceBytes", "--max-source-bytes", "250000000",
                    "--max-dimension-m", "2",
                 ), {"q": "environment", "scope": "online", "provider": "simready",
                     "sort": "sourceBytes", "maxSourceBytes": 250000000,
                     "maxDimensionM": 2.0},
                 {"query": "environment", "scope": "online", "provider": "simready",
                  "sort": "sourceBytes", "maxSourceBytes": 250000000,
                  "maxDimensionM": 2.0}),
                ("approximate", False, ("-q", "cart", "--usage", "dressing", "--approximate"),
                 {"q": "cart", "usage": "dressing", "approximate": True},
                 {"query": "cart", "usage": "dressing", "approximate": True}),
            )
            documents = {}
            for name, _, _, params, arguments in cases:
                documents[name] = http.get(
                    "/api/v1/library/search", params=params
                ).json()
                assert search_view(await result(
                    mcp, "catalog_search", **arguments
                )) == search_view(documents[name])
            cli_cases = [case for case in cases if case[1]]
            cli_filters = await asyncio.gather(*[
                cli_async(environment, "catalog", "search", *flags)
                for _, _, flags, _, _ in cli_cases
            ])
            for (name, _, _, _, _), document in zip(cli_cases, cli_filters, strict=True):
                assert search_view(document) == search_view(documents[name])
            local = documents["installed"]["results"][0]
            assert local["type"] == "local" and local["local"]["asset"]["id"] == local["id"]
            assert local["detail"] == {
                "resource": "asset", "id": local["id"], "tool": "library_describe",
            }
            bare, revision = local["id"].split("@")
            kind, name = bare.split("/")
            assert await result(mcp, "library_describe", id=local["id"]) == http.get(
                f"/api/v1/library/assets/{kind}/{name}/{revision}", params={"view": "summary"}
            ).json()
            approximate = documents["approximate"]
            assert approximate["counts"]["approximate"] == 1
            assert approximate["results"][0]["match"]["unknown"] == ["usage"]
            assert "dressing" in approximate["results"][0]["unknown"]

            # Local exact identities accept the asset: prefix through every surface.
            asset_expected = http.get(
                "/api/v1/library/search",
                params={"q": "asset:robot/fr3@1", "scope": "local"},
            ).json()
            assert [item["id"] for item in asset_expected["results"]] == ["robot/fr3@1"]
            assert search_view(await result(
                mcp, "catalog_search", query="asset:robot/fr3@1", scope="local"
            )) == search_view(asset_expected)
            assert search_view(await cli_async(
                environment, "catalog", "search", "-q", "asset:robot/fr3@1",
                "--scope", "local",
            )) == search_view(asset_expected)

            # Catalog identity accepts a catalog: ref and returns the exact entry documents.
            summary = http.get(ENTRY_ROUTE).json()
            full = http.get(
                ENTRY_ROUTE, params={"view": "full", "documentDigest": summary["documentDigest"]}
            ).json()
            estimate = http.get(ENTRY_ROUTE + "/estimate").json()
            cli_show, cli_estimate = await asyncio.gather(
                cli_async(environment, "catalog", "show", "catalog:" + CATALOG_ID),
                cli_async(environment, "catalog", "estimate", CATALOG_ID),
            )
            assert cli_show == summary and cli_estimate == estimate
            assert await result(mcp, "catalog_show", id=CATALOG_ID) == summary
            assert await result(
                mcp, "catalog_show", id=CATALOG_ID, view="full",
                documentDigest=summary["documentDigest"],
            ) == full
            assert await result(mcp, "catalog_estimate", id=CATALOG_ID) == estimate

            # Cursors page without duplicates or skips; a different limit is allowed.
            ordered = http.get(
                "/api/v1/library/search",
                params={"scope": "online", "sort": "name", "limit": 50},
            ).json()["results"]
            expected_ids = [item["id"] for item in ordered]
            assert len(expected_ids) == 5

            def cli_pages():
                identifiers, cursor = [], None
                while True:
                    arguments = [
                        "catalog", "search", "--scope", "online", "--sort", "name", "--limit", "3",
                    ]
                    if cursor is not None:
                        arguments += ["--cursor", cursor]
                    page = cli(environment, *arguments)
                    identifiers.extend(item["id"] for item in page["results"])
                    cursor = page["nextCursor"]
                    if cursor is None:
                        return identifiers

            cli_ids = await asyncio.to_thread(cli_pages)
            assert cli_ids == expected_ids

            mcp_ids, cursor, limit = [], None, 3
            while True:
                arguments = {"scope": "online", "sort": "name", "limit": limit}
                if cursor is not None:
                    arguments["cursor"] = cursor
                page = await result(mcp, "catalog_search", **arguments)
                mcp_ids.extend(item["id"] for item in page["results"])
                cursor, limit = page["nextCursor"], 2
                if cursor is None:
                    break
            assert mcp_ids == expected_ids
            assert len(set(mcp_ids)) == len(mcp_ids) == len(expected_ids)

            # Structured errors keep their HTTP codes through CLI and MCP.
            invalid = http.get(
                "/api/v1/library/search", params={"q": "box", "cursor": "not-a-cursor"}
            )
            missing_route = (
                "/api/v1/library/catalog/entries/simready/missing_package/" + "0" * 40
            )
            missing = http.get(missing_route)
            assert invalid.status_code == 400
            assert invalid.json()["detail"]["code"] == "invalid_cursor"
            assert missing.status_code == 404
            assert missing.json()["detail"]["code"] == "unknown_catalog_entry"
            cli_invalid, cli_missing = await asyncio.gather(
                cli_async(environment, "catalog", "search", "-q", "box",
                          "--cursor", "not-a-cursor", code=1),
                cli_async(environment, "catalog", "show", UNKNOWN_ID, code=1),
            )
            assert error_code(cli_invalid) == "invalid_cursor"
            assert cli_invalid["error"]["statusCode"] == 400
            assert error_code(cli_missing) == "unknown_catalog_entry"
            assert cli_missing["error"]["statusCode"] == 404
            mcp_invalid = await mcp.call_tool(
                "catalog_search", {"query": "box", "cursor": "not-a-cursor"}
            )
            assert mcp_invalid.is_error
            mcp_invalid_document = mcp_error(mcp_invalid)
            assert mcp_invalid_document["error"]["statusCode"] == 400
            assert error_code(mcp_invalid_document) == "invalid_cursor"
            mcp_missing = await mcp.call_tool("catalog_show", {"id": UNKNOWN_ID})
            assert mcp_missing.is_error
            mcp_missing_document = mcp_error(mcp_missing)
            assert mcp_missing_document["error"]["statusCode"] == 404
            assert error_code(mcp_missing_document) == "unknown_catalog_entry"

            conflict = await cli_async(
                environment, "catalog", "search", "-q", "box",
                "--installed", "--not-installed", code=1,
            )
            assert conflict["error"]["statusCode"] == 400
            assert error_code(conflict) == "invalid_filter"
            mcp_conflict = await mcp.call_tool(
                "catalog_search", {"query": "box", "installed": True, "notInstalled": True}
            )
            assert mcp_conflict.is_error
            assert "invalid_filter" in mcp_conflict.content[0].text


async def test_local_gap_filter_uses_same_http_behavior_through_installed_cli_and_mcp(gateway_url):
    environment = client_environment(gateway_url)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=20) as http:
        upload_correlated_box(http)
        params = {"q": "CLI box", "scope": "local", "kind": "environment", "gaps": True}
        expected = http.get("/api/v1/library/search", params=params)
        assert expected.status_code == 200, expected.text
        document = expected.json()
        assert [item["id"] for item in document["results"]] == ["environment/cli-box@1"]
        assert document["results"][0]["local"]["asset"]["gapCount"] > 0
        assert search_view(await cli_async(
            environment, "catalog", "search", "-q", "CLI box", "--scope", "local",
            "--kind", "environment", "--gaps",
        )) == search_view(document)
        legacy = http.get("/api/v1/library/assets", params={
            "q": "CLI box", "kind": "environment", "gaps": True,
        }).json()["assets"]
        assert await cli_async(
            environment, "library", "search", "-q", "CLI box", "--kind", "environment", "--gaps",
        ) == legacy
        async with Client(transport, mode="legacy") as mcp:
            assert search_view(await result(
                mcp, "catalog_search", query="CLI box", scope="local", kind="environment", gaps=True,
            )) == search_view(document)
            assert await result(
                mcp, "library_search", query="CLI box", kind="environment", gaps=True,
            ) == legacy
            complete = http.get("/api/v1/library/search", params={**params, "gaps": False}).json()
            assert complete["results"] == []
            assert search_view(await result(
                mcp, "catalog_search", query="CLI box", scope="local", kind="environment", gaps=False,
            )) == search_view(complete)
            rejected = await mcp.call_tool("catalog_search", {"scope": "online", "gaps": True})
            assert rejected.is_error
            assert error_code(mcp_error(rejected)) == "invalid_filter"
        rejected = await cli_async(
            environment, "catalog", "search", "--scope", "online", "--gaps", code=1,
        )
        assert rejected["error"]["statusCode"] == 400 and error_code(rejected) == "invalid_filter"
