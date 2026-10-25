"""Authoring Report submit/list/show via HTTP, installed lw and MCP stdio.

The fixture gateway runs the production report store over the real built-in
library. No attachment, path or external message is accepted; the fixture has no
network, Blender or physics dependency.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from cli_support import LW, ROOT, result, stop
from mcp import Client, StdioServerParameters

UNKNOWN = "sub-unknown"


def launch(directory):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = os.environ | {
        "SPATIAL_RECORDINGS_ROOT": str(directory / "recordings"),
        "SPATIAL_STATE_ROOT": str(directory / "state"),
        "LW_LIBRARY": str(directory / "user-library"),
    }
    log = (directory / "gateway.log").open("wb")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "authoring_gateway:app",
            "--app-dir", "tests/fixtures", "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=ROOT, stdout=log, stderr=log, env=environment,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=url, trust_env=False, timeout=1) as http:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, (directory / "gateway.log").read_text()
                try:
                    if http.get("/api/v1/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.02)
            else:
                raise AssertionError("Authoring fixture gateway did not become ready")
    except BaseException:
        stop(process)
        raise
    return url, process, log


@pytest.fixture(scope="module")
def gateway(tmp_path_factory):
    url, process, log = launch(tmp_path_factory.mktemp("authoring-gateway"))
    try:
        yield url
    finally:
        stop(process)
        log.close()


def environment_for(gateway_url):
    return {key: value for key, value in os.environ.items() if key != "LW_SESSION"} | {
        "LW_URL": gateway_url,
        "PYTHONIOENCODING": "utf-8",
    }


def cli(environment, *arguments, code=0):
    invocation = subprocess.run(
        [str(LW), *arguments, "--json"],
        cwd=ROOT, env=environment, capture_output=True, text=True,
        encoding="utf-8", timeout=20,
    )
    expected = code if isinstance(code, tuple) else (code,)
    assert invocation.returncode in expected, (arguments, invocation.stdout, invocation.stderr)
    return json.loads(invocation.stdout)


async def cli_async(environment, *arguments, code=0):
    return await asyncio.to_thread(cli, environment, *arguments, code=code)


def submit(_http, content_hash, submission_id, **overrides):
    document = {
        "submissionId": submission_id,
        "source": "claude-code/scene-director",
        "sourceVersion": "2.1",
        "reportedAt": "2026-10-24T09:30:00+00:00",
        "draftId": "draft-cart",
        "object": {"kind": "asset", "id": "robot/fr3@1", "contentHash": content_hash},
        "summary": "外部汇报：台车已移动到 0.20 m；尚未导入。",
        "gaps": ["未导出新场景包"],
        "artifacts": [{"name": "preview", "reference": "artifact://cart/preview.png"}],
    }
    return document | overrides


def error_code(document):
    return document["error"]["detail"]["code"]


def mcp_error(document):
    text = document.content[0].text
    return json.loads(text[text.index("{"):])


async def test_submit_list_show_http_cli_and_mcp_share_one_document(gateway, tmp_path):
    environment = environment_for(gateway)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    with httpx.Client(base_url=gateway, trust_env=False, timeout=30) as http:
        asset = http.get("/api/v1/library/assets/robot/fr3/1").json()
        first_document = submit(http, asset["contentHash"], "sub-cli")
        first_file = tmp_path / "first.json"
        first_file.write_text(json.dumps(first_document, ensure_ascii=False), encoding="utf-8")

        submitted = cli(environment, "authoring", "submit", str(first_file))
        assert submitted["submissionId"] == "sub-cli"
        assert submitted["object"] == first_document["object"]
        assert submitted["summary"] == first_document["summary"]
        assert submitted["reportedAt"] == "2026-10-24T09:30:00+00:00"
        assert submitted["receivedAt"]
        assert http.get("/api/v1/library/authoring-reports/sub-cli").json() == submitted
        assert cli(environment, "authoring", "show", "sub-cli") == submitted

        # The same submissionId never rewrites the first receipt.
        replay = cli(environment, "authoring", "submit", str(first_file))
        assert replay == submitted
        changed_file = tmp_path / "changed.json"
        changed_file.write_text(json.dumps(
            first_document | {"summary": "different local text"}, ensure_ascii=False
        ), encoding="utf-8")
        assert cli(environment, "authoring", "submit", str(changed_file)) == submitted

        async with Client(transport, mode="legacy") as mcp:
            second_document = submit(
                http, asset["contentHash"], "sub-mcp", draftId="draft-mcp",
                reportedAt="2026-10-23T08:00:00+00:00", gaps=[],
            )
            mcp_submitted = await result(mcp, "authoring_submit", document=second_document)
            assert mcp_submitted["submissionId"] == "sub-mcp"
            assert mcp_submitted == http.get(
                "/api/v1/library/authoring-reports/sub-mcp"
            ).json() == await cli_async(environment, "authoring", "show", "sub-mcp")

            listing = http.get("/api/v1/library/authoring-reports").json()
            assert [item["submissionId"] for item in listing["reports"]] == [
                "sub-cli", "sub-mcp",
            ]
            assert await result(mcp, "authoring_list") == listing
            assert await cli_async(environment, "authoring", "ls") == listing

            asset_filtered = http.get(
                "/api/v1/library/authoring-reports", params={"asset": "robot/fr3@1"}
            ).json()
            assert [item["submissionId"] for item in asset_filtered["reports"]] == [
                "sub-cli", "sub-mcp",
            ]
            assert await result(
                mcp, "authoring_list", asset="robot/fr3@1"
            ) == asset_filtered == await cli_async(
                environment, "authoring", "ls", "--asset", "robot/fr3@1"
            )
            draft_filtered = http.get(
                "/api/v1/library/authoring-reports", params={"draft": "draft-mcp"}
            ).json()
            assert [item["submissionId"] for item in draft_filtered["reports"]] == ["sub-mcp"]
            assert await result(
                mcp, "authoring_list", draft="draft-mcp"
            ) == draft_filtered == await cli_async(
                environment, "authoring", "ls", "--draft", "draft-mcp"
            )
            one = http.get(
                "/api/v1/library/authoring-reports", params={"limit": 1}
            ).json()
            assert [item["submissionId"] for item in one["reports"]] == ["sub-cli"]
            assert await result(mcp, "authoring_list", limit=1) == one == await cli_async(
                environment, "authoring", "ls", "--limit", "1"
            )


async def test_invalid_binding_and_unknown_report_keep_existing_envelopes(gateway, tmp_path):
    environment = environment_for(gateway)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    with httpx.Client(base_url=gateway, trust_env=False, timeout=30) as http:
        asset = http.get("/api/v1/library/assets/robot/fr3/1").json()
        invalid = submit(
            http, asset["contentHash"], "sub-invalid", path="C:\\secret\\scene.blend",
        )
        invalid_file = tmp_path / "invalid.json"
        invalid_file.write_text(json.dumps(invalid), encoding="utf-8")
        rejected = cli(environment, "authoring", "submit", str(invalid_file), code=1)
        assert rejected["error"]["statusCode"] == 422
        assert error_code(rejected) == "invalid_authoring_report"
        wrong_hash = submit(http, "b" * 64, "sub-wrong-hash")
        wrong_file = tmp_path / "wrong-hash.json"
        wrong_file.write_text(json.dumps(wrong_hash), encoding="utf-8")
        wrong = cli(environment, "authoring", "submit", str(wrong_file), code=1)
        assert wrong["error"]["statusCode"] == 422
        assert error_code(wrong) == "invalid_authoring_report"

        missing = cli(environment, "authoring", "show", UNKNOWN, code=1)
        assert missing["error"]["statusCode"] == 404
        assert error_code(missing) == "unknown_authoring_report"
        assert http.get(f"/api/v1/library/authoring-reports/{UNKNOWN}").json()["detail"][
            "code"
        ] == "unknown_authoring_report"

        async with Client(transport, mode="legacy") as mcp:
            mcp_missing = await mcp.call_tool("authoring_show", {"id": UNKNOWN})
            assert mcp_missing.is_error
            assert error_code(mcp_error(mcp_missing)) == "unknown_authoring_report"
            mcp_invalid = await mcp.call_tool("authoring_submit", {"document": {
                "submissionId": "sub-mcp-invalid", "source": "x", "sourceVersion": "1",
                "reportedAt": "2026-10-24T09:30:00Z",
                "attachments": [{"name": "scene.blend"}],
            }})
            assert mcp_invalid.is_error
            assert error_code(mcp_error(mcp_invalid)) == "invalid_authoring_report"
