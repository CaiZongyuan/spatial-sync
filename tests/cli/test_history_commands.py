"""Installed lw and the official stdio MCP client mirror HTTP points and cap metadata."""

import json
import shutil
import struct

from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters

KEY = "/World/Lab/Centrifuge"


async def test_history_cli_and_mcp_mirror_active_and_explicit_recordings(lw, lab):
    lw("do", KEY, "closeLid")
    lw("session", "pause")
    params = {"key": KEY, "property": "lidOpen", "since": "1h", "every": "1m", "agg": "last"}
    response = lab[0].get("/api/v1/history", params=params)
    assert response.status_code == 200, response.text
    # A pause transition can still publish its final reliable state. Freeze the
    # requested upper bound so independent calls compare the same time window.
    params["until"] = response.headers["x-history-until-ms"]
    expected = {"points": response.json(), "recordingId": response.headers["x-history-recording"],
                "limit": 2000, "truncated": False,
                "sinceMs": float(response.headers["x-history-since-ms"]),
                "untilMs": float(response.headers["x-history-until-ms"]),
                "everyMs": 60_000, "agg": "last", "message": None}
    assert expected["points"] and expected["points"][-1][1] is False
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=lab[2])
    async with Client(transport, mode="legacy") as mcp:
        tool = next(tool for tool in (await mcp.list_tools()).tools if tool.name == "history_query")
        assert set(tool.input_schema["required"]) == {"key", "property", "since"}
        assert lw("history", KEY, "lidOpen", "--since", "1h", "--every", "1m", "--agg", "last",
                  "--until", params["until"]) == expected
        assert await result(mcp, "history_query", **params) == expected
        sid = expected["recordingId"]
        lab[0].delete(lab[1]).raise_for_status()
        explicit = params | {"recording": sid}
        public = lab[0].get("/api/v1/history", params=explicit)
        retained = lw("history", KEY, "lidOpen", "--since", "1h", "--every", "1m", "--agg", "last",
                      "--recording", sid, "--until", params["until"])
        assert retained["points"] == public.json()
        assert await result(mcp, "history_query", **explicit) == retained
        assert lw("history", "/Real/Incubator2", "temperatureC", "--since", "1h", code=1)[
            "error"]["detail"]["code"] == "live_unavailable"
        denied = await mcp.call_tool("history_query", {"key": "/Real/Incubator2", "property": "temperatureC",
                                                       "since": "1h"})
        assert denied.is_error and "live_unavailable" in denied.content[0].text
        invalid = lw("history", KEY, "lidOpen", "--since", "1h", "--every", "0s", "--recording", sid, code=1)
        assert invalid["error"]["statusCode"] == 422


async def test_history_cap_explanation_survives_installed_cli_and_official_mcp(lw, lab, gateway_directory):
    # An external producer can publish old LWLG v1 recordings without sidecars.
    # Extend the retained fixture with 2001 independently specified measurements.
    directory = gateway_directory / "recordings/history-cli-cap"
    shutil.copytree(ROOT / "tests/fixtures/recordings/v1", directory)
    metadata = json.loads((directory / "recording.json").read_bytes())
    segment = metadata["segments"][-1]
    start = segment["endSampleTimeMs"] + 1
    with (directory / segment["file"]).open("ab") as output:
        for index in range(2001):
            message = {"type": "entity.observations", "sceneRevision": metadata["sceneRevision"],
                       "epoch": 0, "simTime": 100 + index, "sampleTimeMs": start + index, "source": "virtual",
                       "changes": [{"key": "/World/Lab/RobotA", "rev": 1_000_000 + index,
                                    "values": {"gripperWidth": index}}]}
            payload = json.dumps(message).encode()
            output.write(struct.pack("<IBd", 9 + len(payload), 2, start + index) + payload)
    segment.update(bytes=(directory / segment["file"]).stat().st_size, endSampleTimeMs=start + 2000)
    metadata["sessionId"] = "history-cli-cap"
    (directory / "recording.json").write_text(json.dumps(metadata))
    params = {"key": "/World/Lab/RobotA", "property": "gripperWidth", "since": str(start),
              "recording": "history-cli-cap"}
    response = lab[0].get("/api/v1/history", params=params)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 2000
    assert response.json()[0] == [start, 0] and response.json()[-1] == [start + 1999, 1999]
    assert response.headers["x-history-truncated"] == "true"
    params["until"] = response.headers["x-history-until-ms"]
    cli = lw("history", params["key"], params["property"], "--since", params["since"],
             "--until", params["until"], "--recording", params["recording"])
    assert cli["points"] == response.json()
    assert cli["truncated"] is True and cli["limit"] == 2000
    assert cli["message"] == response.headers["x-history-explanation"]
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=lab[2])
    async with Client(transport, mode="legacy") as mcp:
        assert await result(mcp, "history_query", **params) == cli
