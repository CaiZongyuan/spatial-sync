import json

from cli_support import LW, result
from mcp import Client, StdioServerParameters

SUITE = "suite/lab-demo-pick-place@1"


async def test_evaluation_and_explicit_launch_tickets_share_cli_mcp_http_without_private_inputs(lw, lab):
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=lab[2])
    async with Client(transport, mode="legacy") as mcp:
        suite = lab[0].get("/api/v1/suites/lab-demo-pick-place/1").json()
        assert lw("suite", "show", SUITE) == await result(mcp, "suite_show", suite=SUITE) == suite
        assert lw("suite", "ls") == await result(mcp, "suite_list") == [suite]
        assert not any(
            private in json.dumps(suite) for private in ("4101", "4102", "9101", "held-out-example")
        )
        first = lw("eval", "run", "robot_arm.virtual", "--suite", SUITE)
        second = await result(mcp, "evaluation_run", subject="robot_arm.virtual", suite=SUITE)
        assert (
            first["status"] == second["status"] == "queued"
        )  # This test owns the existing interactive slot.
        assert lw("eval", "show", first["evaluationId"]) == await result(
            mcp,
            "evaluation_show",
            id=first["evaluationId"],
        )
        assert lw("eval", "ls") == await result(mcp, "evaluation_list")
        assert lw("score", "ls", "--suite", SUITE) == await result(mcp, "score_list", suite=SUITE) == []
        queued = await result(mcp, "scene_launch", sceneVersion="lab-demo@3", seed=86)
        assert queued["status"] == "queued" and "sessionId" not in queued
        assert lw("session-request", "show", queued["requestId"]) == await result(
            mcp,
            "session_request_show",
            id=queued["requestId"],
        )
        assert lw("session-request", "cancel", queued["requestId"], code=3)["status"] == "cancelled"
        assert (await result(mcp, "session_request_cancel", id=queued["requestId"]))["status"] == "cancelled"
        assert lw("eval", "cancel", first["evaluationId"], code=3)["status"] == "cancelled"
        assert (await result(mcp, "evaluation_cancel", id=second["evaluationId"]))["status"] == "cancelled"
        reused = lab[0].post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3", "source": "synthetic"})
        assert reused.status_code == 201 and reused.json()["sessionId"] == lab[2]["LW_SESSION"]
        before = lab[0].get("/api/v1/health").json()["sessions"]
        rejected = lw("eval", "run", "not_a_bound_program", "--suite", SUITE, code=1)
        assert rejected["error"]["statusCode"] == 422
        assert lab[0].get("/api/v1/health").json()["sessions"] == before
