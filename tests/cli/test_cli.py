"""Installed lw subprocesses exercise real HTTP operations and all exit classes."""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from cli_support import LW, ROOT, stop
from lw.__main__ import parameters

CENTRIFUGE = "/World/Lab/Centrifuge"
ROBOT = "/World/Lab/RobotA"


def test_cli_inspection_actions_queries_controls_and_recordings(lw, lab):
    assert lw("status")["sceneId"] == "lab-demo"
    assert [item["key"] for item in lw("ls", "--type", "centrifuge/v1")] == [CENTRIFUGE]
    assert "actions" in lw("show", CENTRIFUGE)["definition"]
    assert lw("get", CENTRIFUGE, "lidOpen") is True
    assert lw("get", CENTRIFUGE)["phase"] == "idle"
    assert (
        lw("do", CENTRIFUGE, "start", "rpm=500", "durationSeconds=1", code=2)["error"]["code"]
        == "precondition_failed"
    )
    invalid = lw("do", CENTRIFUGE, "start", "rpm=fast", "durationSeconds=1", code=2)
    assert invalid["params"]["rpm"] == "fast" and invalid["actor"] == {"kind": "cli", "name": "lw"}
    unicode_error = lw(
        "do",
        CENTRIFUGE,
        "start",
        "rpm=中文",
        "durationSeconds=1",
        code=2,
        env=lab[2] | {"PYTHONIOENCODING": "ascii"},
    )
    assert unicode_error["params"]["rpm"] == "中文"
    closed = lw("do", CENTRIFUGE, "closeLid", "--wait")
    assert closed["status"] == "succeeded"
    assert lw("inv", closed["invocationId"], "--wait")["invocationId"] == closed["invocationId"]
    assert lw("approve", closed["invocationId"])["status"] == "succeeded"
    assert lw("approve", closed["invocationId"], "--deny")["status"] == "succeeded"
    assert lw("wait", CENTRIFUGE, "lidOpen", "equals", "false")["satisfied"] is True
    assert lw("session", "pause")["state"]["state"] == "PAUSED"
    stepped = lw("session", "step", "12")
    assert stepped["ack"]["status"] == "applied"
    assert lw("session", "reset")["state"]["simTime"] == 0
    assert lw("session", "resume")["state"]["state"] == "RUNNING"
    assert lw("rec", "ls")[0]["sessionId"] == lab[2]["LW_SESSION"]


def test_cli_arrays_cancellation_timeout_usage_and_connection_errors(lw, lab):
    moved = lw("do", ROBOT, "moveJoints", "positions=[0,0.1,0,0,0,0,0]", "speed=1")
    assert moved["status"] == "succeeded" and moved["params"]["positions"][1] == 0.1
    lw("session", "pause")
    pending = lw("do", ROBOT, "moveJoints", "positions=[0,0,0,0,0,0,0]", "--no-wait")
    assert pending["status"] == "accepted"
    assert lw("inv", pending["invocationId"], "--wait", "--timeout", "0.01", code=4)["timedOut"]
    assert lw("wait", CENTRIFUGE, "lidOpen", "equals", "false", "--timeout", "0.01", code=4)["timedOut"]
    # Cancellation publication is reliable and can be consumed before this process prints its direct reply.
    result = subprocess.run(
        [str(LW), "cancel", pending["invocationId"], "--json"],
        env=lab[2],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode in (0, 3)
    assert lw("inv", pending["invocationId"], "--wait", code=3)["status"] == "cancelled"
    assert lw("do", code=1)["error"]
    assert lw("do", ROBOT, "home", "malformed", code=1)["error"]
    assert lw("inv", "unknown", code=1)["error"]["statusCode"] == 404
    assert lw("status", env=lab[2] | {"LW_URL": "http://127.0.0.1:1"}, code=1)["error"]
    assert (
        lw("status", env=lab[2] | {"LW_URL": "http://localhost:invalid"}, code=1)["error"]["message"]
        == "Invalid LW_URL"
    )
    assert parameters(["a=true", "b=null", "c=[1,2]", "d=text"]) == {
        "a": True,
        "b": None,
        "c": [1, 2],
        "d": "text",
    }


def test_cli_unique_session_selection_and_human_output(lw, lab, tmp_path):
    environment = {key: value for key, value in lab[2].items() if key != "LW_SESSION"}
    assert lw("status", env=environment)["sessionId"] == lab[2]["LW_SESSION"]
    human = subprocess.run(
        [str(LW), "status"],
        env=lab[2],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert human.returncode == 0 and "lab-demo" in human.stdout and "simTime=" in human.stdout
    lab[0].delete(lab[1])
    assert "found 0" in lw("status", env=environment, code=1)["error"]["message"]


def test_cli_watch_streams_only_the_selected_entity(lab):
    watcher = subprocess.Popen(
        [str(LW), "watch", CENTRIFUGE, "--json"],
        env=lab[2],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            line = pool.submit(watcher.stdout.readline)
            deadline = time.monotonic() + 5
            while not line.done() and time.monotonic() < deadline:
                for key in (ROBOT, CENTRIFUGE):
                    lab[0].post(
                        lab[1] + "/actions",
                        json={
                            "requestId": f"watch-{key}-{time.monotonic_ns()}",
                            "entityKey": key,
                            "action": "missing",
                        },
                    )
                time.sleep(0.02)
            if not line.done():
                stop(watcher)
            message = json.loads(line.result(timeout=2))
            assert message["type"] == "invocation.update" and message["entityKey"] == CENTRIFUGE
    finally:
        stop(watcher)


def test_default_human_rejection_includes_condition_next_step(lab):
    rejected = subprocess.run(
        [str(LW), "do", CENTRIFUGE, "start", "rpm=3000", "durationSeconds=30"],
        env=lab[2],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert rejected.returncode == 2
    assert "The lid is open" in rejected.stdout and "call closeLid first" in rejected.stdout
