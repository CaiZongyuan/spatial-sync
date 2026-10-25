"""Installed CLI/official MCP enter one promotion transaction; scores/qualification are controlled."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx
from cli_support import LW, ROOT, result, stop
from mcp import Client, Implementation, StdioServerParameters

from tests.fixtures.policy import observation_spec
from tests.gateway.test_policies import uploaded_job


def terminal_approve(approval, env):
    import select

    master, slave = os.openpty()
    process = subprocess.Popen([str(LW), "approve", approval, "--note", "Controlled human PTY decision"],
                               cwd=ROOT, env=env, stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    received, confirmed = b"", False
    deadline = time.monotonic() + 30
    try:
        while process.poll() is None:
            assert time.monotonic() < deadline, received.decode("utf-8", errors="replace")
            if select.select([master], [], [], 0.1)[0]:
                try:
                    received += os.read(master, 65536)
                except OSError:
                    break
            if not confirmed and b'Type "approve"' in received:
                os.write(master, b"approve\n")
                confirmed = True
        process.wait(timeout=5)
        assert confirmed and process.returncode == 0, received.decode("utf-8", errors="replace")
        assert b'"candidateBelowReference": true' in received and b'"humanReview"' in received
    finally:
        os.close(master)
        stop(process)


async def test_installed_clients_request_private_review_and_observe_actual_publication(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = os.environ | {"LW_URL": url, "LW_SESSION": "", "PYTHONIOENCODING": "utf-8",
                        "LW_LIBRARY": str(tmp_path / "library"),
                        "SPATIAL_STATE_ROOT": str(tmp_path / "state"),
                        "SPATIAL_RECORDINGS_ROOT": str(tmp_path / "recordings")}
    with (tmp_path / "gateway.log").open("wb") as log:
        process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "controller_promotion_gateway:app",
            "--app-dir", "tests/fixtures",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=url, trust_env=False, timeout=30) as http:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, (tmp_path / "gateway.log").read_text(encoding="utf-8")
                    try:
                        if http.get("/api/v1/health", timeout=0.5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline
                    time.sleep(0.02)

                def invoke(*arguments):
                    value = subprocess.run([str(LW), *arguments, "--json"], cwd=ROOT, env=env,
                                           capture_output=True, text=True, encoding="utf-8", timeout=20)
                    assert value.returncode == 0, value.stdout + value.stderr
                    return json.loads(value.stdout)

                spec = observation_spec()
                spec["observations"][0]["keys"] = spec["action"]["jointKeys"] = [
                    "/World/Lab/RobotA/fr3/fr3_joint1"
                ]
                job = uploaded_job(http, tmp_path, spec=spec)
                policy = invoke("policy", "create", job["jobId"] + "/checkpoints/model_0.pt")
                seeded = http.post("/fixture/controller-promotions/train", json={"policy": policy["id"]})
                assert seeded.status_code == 200, seeded.text
                assert seeded.json()["sourceStarts"] == 0 and seeded.json()["evidenceMode"] == "controlled"
                transport = StdioServerParameters(command=str(LW), args=["mcp"], env=env)
                async with Client(transport, mode="legacy",
                                  client_info=Implementation(name="promotion-optimizer", version="1")) as mcp:
                    names = {tool.name for tool in (await mcp.list_tools()).tools}
                    assert {"policy_promote", "controller_promotion_show"} <= names
                    assert not {"controller_promotion_review", "controller_promotion_approve"} & names
                    promotion = await result(mcp, "policy_promote", id=policy["id"], scene=policy["scene"],
                                             request_id="controlled-installed-clients")
                    assert promotion["status"] == "testing"
                    repeated = invoke("policy", "promote", policy["id"], "--scene", policy["scene"],
                                      "--request-id", "controlled-installed-clients")
                    assert repeated == promotion
                    completed = http.post(
                        "/fixture/controller-promotions/" + promotion["id"] + "/complete-tests"
                    )
                    assert completed.status_code == 200, completed.text
                    public = invoke("promotion", "show", promotion["id"])
                    assert public == await result(mcp, "controller_promotion_show", id=promotion["id"])
                    assert "test" not in public and "candidateBelowReference" not in public
                    review = http.get("/api/v1/controller-promotions/" + promotion["id"] + "/review",
                                      headers={"X-Actor": "user:fixture-human"})
                    assert review.status_code == 200 and review.json()["candidateBelowReference"], review.text
                    if os.name == "posix":
                        terminal_approve(public["approvalId"], env)
                        audit = http.get("/api/v1/approvals/" + public["approvalId"]).json()
                        assert audit["decisions"][-1]["actor"] == {"kind": "user", "name": "cli"}
                    else:
                        published = http.post("/api/v1/approvals/" + public["approvalId"] + "/decision",
                                              headers={"X-Actor": "user:fixture-human"},
                                              json={"decision": "approve"})
                        assert published.status_code == 200, published.text
                        assert published.json()["status"] == "published", published.text
                    same = invoke("promotion", "show", promotion["id"])
                    assert same["status"] == "published"
                    assert same == await result(mcp, "controller_promotion_show", id=promotion["id"])
                    controller = invoke("controller", "show", "fr3-arm")
                    assert controller["current"]["policy"] == policy["id"]
                    assert controller["current"]["action"]["hz"] == 30.0
                    assert http.get("/api/v1/health").json()["sessionCount"] == 0
        finally:
            stop(process)
