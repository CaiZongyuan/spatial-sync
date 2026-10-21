"""Run the I5 journey: Claude Code completes the I3 flow through `lw mcp` alone.

The agent runs headless in an empty directory with no built-in tools, so the lab
MCP server is its only interface. An independent HTTP observer checks every
settled tube location against physics while it works; afterwards the runner
checks the invocation log, closes the session and packages its recording.
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from smoke_info_newton import CENTRIFUGE, physical_check

from packages.protocol.schema import SceneManifest, TransformFrame

TUBES = {f"/World/Lab/Tubes/Tube{index:02d}": index for index in (1, 2, 3, 4)}
FINAL = {"/World/Lab/Tubes/Tube01": "/World/Lab/Rack/A1", "/World/Lab/Tubes/Tube02": "/World/Lab/Rack/A2"}
PROMPT = """你在操作一个物理仿真实验室，只能使用 lab MCP 工具。请完成这个实验：

1. 在场景库里找到 robot-and-tubes，用 Newton 物理源启动。
2. 把架子 A1 的试管放进离心机 1 号孔位，把 A2 的试管放进 3 号孔位。
3. 关上离心机盖，以 3000 rpm 离心 20 秒，等离心完成。
4. 打开盖子，把两支试管分别放回架子 A1 和 A2。

动作被拒绝时，读拒绝原因和提示，自行调整后继续。完成后用两三句话总结你做了什么、遇到了哪些拒绝。"""


def pick(source, target):
    return ("pickPlace", {"from": source, "to": target})


def journey_order(invocations):
    """Follow I3's causal order through the succeeded actions; rejections may appear anywhere."""
    done = [(item["action"], item["params"]) for item in invocations if item["status"] == "succeeded"]

    def after(step, index):
        if index is None:
            return None
        return next((i for i, item in enumerate(done) if i > index and item == step), None)

    steps = {"slot1": after(pick("rack.A1", "centrifuge.slot1"), -1)}
    steps["slot3"] = after(pick("rack.A2", "centrifuge.slot3"), -1)
    placed = None if None in steps.values() else max(steps.values())
    steps["closeLid"] = after(("closeLid", {}), placed)
    steps["start"] = after(("start", {"rpm": 3000, "durationSeconds": 20}), steps["closeLid"])
    steps["openLid"] = after(("openLid", {}), steps["start"])
    steps["rackA1"] = after(pick("centrifuge.slot1", "rack.A1"), steps["openLid"])
    steps["rackA2"] = after(pick("centrifuge.slot3", "rack.A2"), steps["openLid"])
    return {"steps": steps, "ordered": None not in steps.values()}


class Observer:
    def __init__(self, client, route, manifest):
        self.client, self.route, self.manifest = client, route, manifest
        self.started = time.monotonic()
        self.events, self.checks, self.centrifuge = [], [], []

    async def values(self):
        response = await self.client.get(self.route + "/state")
        response.raise_for_status()
        return {key: value["values"] for key, value in response.json()["entities"].items()}

    async def frame(self):
        response = await self.client.get(self.route + "/snapshot")
        response.raise_for_status()
        return TransformFrame.model_validate(response.json())

    async def check(self, key, expected):
        previous = await self.frame()
        await asyncio.sleep(0.12)
        snapshot = await self.frame()
        values = await self.values()
        record = {"key": key, "expected": expected, "simTime": snapshot.simTime}
        try:
            record |= physical_check(self.manifest, previous, snapshot, values, TUBES[key], expected)
            record["passed"] = True
        except AssertionError as error:
            record |= {"passed": False, "observed": values[key]["location"], "error": str(error)}
        self.checks.append(record)
        return record["passed"]

    async def watch(self, stop):
        last, due = {}, {}
        while not stop.is_set():
            values, now = await self.values(), time.monotonic()
            centrifuge = {name: values[CENTRIFUGE][name] for name in ("phase", "lidOpen", "rpm")}
            if not self.centrifuge or self.centrifuge[-1]["state"] != centrifuge:
                self.centrifuge.append({"t": round(now - self.started, 3), "state": centrifuge})
            for key in TUBES:
                location = values[key]["location"]
                if location != last.get(key):
                    last[key] = location
                    self.events.append({"t": round(now - self.started, 3), "key": key, "location": location})
                    # A Site location is checked once it has settled; held/loose have no Site to compare.
                    due[key] = now + 0.4 if location.startswith("/World/") else None
            # The I3 oracle compares world velocity, valid only while the rotor is at rest.
            resting = values[CENTRIFUGE]["rpm"] == 0
            for key, deadline in list(due.items()):
                if deadline is not None and now >= deadline and resting:
                    due[key] = None
                    await self.check(key, last[key])
            await asyncio.sleep(0.2)


async def sessions(client, states):
    """Health keeps stopped sessions listed, so callers select the lifecycle states they mean."""
    listed = (await client.get("/api/v1/health")).json()["sessions"]
    return [session for session in listed if session["state"] in states]


async def wait_for_session(client, agent):
    """The agent launches the scene itself; None means it exited without a session."""
    while True:
        if ready := await sessions(client, {"RUNNING", "PAUSED"}):
            return ready[0]["sessionId"]
        if agent.done():
            return None
        await asyncio.sleep(0.25)


async def run_agent(command, cwd, transcript, timeout):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CLAUDECODE")}
    # One stream-json line carries a whole tool result, e.g. a scene description.
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=64 * 1024 * 1024,
    )
    tools, result = [], None
    try:
        async with asyncio.timeout(timeout):
            with transcript.open("wb") as file:
                async for line in process.stdout:
                    file.write(line)
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if message.get("type") == "assistant":
                        for item in message["message"].get("content", []):
                            if item.get("type") == "tool_use":
                                tools.append(item["name"])
                                step = {"tool": item["name"], "input": item["input"]}
                                print(json.dumps(step, ensure_ascii=False), flush=True)
                    elif message.get("type") == "result":
                        result = message
            await process.wait()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    stderr = (await process.stderr.read()).decode(errors="replace")
    return {"exitCode": process.returncode, "tools": tools, "result": result, "stderr": stderr[-4000:]}


async def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt = {"api": args.api, "prompt": PROMPT, "failures": []}

    def fail(reason):
        receipt["failures"].append(reason)

    async with httpx.AsyncClient(base_url=args.api, timeout=60, trust_env=False) as client:
        if live := await sessions(client, {"CREATED", "LOADING", "READY", "RUNNING", "PAUSED", "STOPPING"}):
            raise SystemExit(f"Close the active session first: {live}")
        config = output / "mcp.json"
        lab = {"command": "uv", "args": ["--directory", str(ROOT), "run", "--no-sync", "lw", "mcp"]}
        config.write_text(json.dumps({"mcpServers": {"lab": lab | {"env": {"LW_URL": args.api}}}}, indent=2))
        command = [
            args.claude,
            "-p",
            PROMPT,
            "--mcp-config",
            str(config),
            "--strict-mcp-config",
            "--tools",
            "",
            "--allowedTools",
            "mcp__lab",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
        ] + (["--model", args.model] if args.model else [])
        receipt["command"] = command[:2] + ["<prompt>"] + command[3:]
        stop, viewer, route = asyncio.Event(), None, None
        agent = observer = watcher = None
        try:
            with tempfile.TemporaryDirectory(prefix="lw-agent-") as cwd:
                agent = asyncio.create_task(
                    run_agent(command, cwd, output / "transcript.jsonl", args.timeout)
                )
                session = await wait_for_session(client, agent)
                if session:
                    route = f"/api/v1/sessions/{session}"
                    receipt["sessionId"] = session
                    manifest = SceneManifest.model_validate((await client.get(route + "/scene")).json())
                    observer = Observer(client, route, manifest)
                    watcher = asyncio.create_task(observer.watch(stop))
                    if args.web:
                        viewer = await asyncio.create_subprocess_exec(
                            str(ROOT / "node_modules/.bin/tsx"),
                            "tools/agent_journey_viewer.ts",
                            "--web",
                            args.web,
                            "--api",
                            args.api,
                            "--session",
                            session,
                            "--output",
                            str(output / "viewer"),
                            cwd=ROOT,
                        )
                receipt["agent"] = await agent
            if not route:
                fail("agent did not launch a session")
                return receipt
            stop.set()
            await watcher
            for key, expected in FINAL.items():
                if not await observer.check(key, expected):
                    fail(f"final location of {key} is not {expected}")
            receipt |= {"locationEvents": observer.events, "physicalChecks": observer.checks}
            receipt["centrifugeSamples"] = observer.centrifuge
            receipt["stats"] = (await client.get(route + "/stats")).json()
            invocations = (await client.get(route + "/invocations")).json()["invocations"]
            invocations.sort(key=lambda item: item["createdAtMs"])
            receipt["invocations"] = [
                {
                    name: item.get(name)
                    for name in ("action", "entityKey", "params", "status", "error", "actor")
                }
                for item in invocations
            ]
            receipt["order"] = journey_order(invocations)
            if not receipt["order"]["ordered"]:
                fail("the succeeded actions do not complete the I3 order")
            if any(item["actor"]["kind"] != "agent" for item in invocations):
                fail("an invocation was not made by the agent")
            if not all(name.startswith("mcp__lab__") for name in receipt["agent"]["tools"]):
                fail("the agent used a tool other than lab MCP")
            if any(not check["passed"] for check in observer.checks):
                fail("a settled tube location disagreed with physics")
            if receipt["agent"]["exitCode"] != 0:
                fail("Claude Code exited with an error")
        finally:
            stop.set()
            for task in (agent, watcher):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (agent, watcher) if task), return_exceptions=True)
            if viewer:
                if viewer.returncode is None:
                    viewer.send_signal(signal.SIGTERM)
                receipt["viewerExitCode"] = await viewer.wait()
                viewer_path = output / "viewer/viewer.json"
                receipt["viewer"] = json.loads(viewer_path.read_text()) if viewer_path.exists() else None
                if not receipt["viewer"] or receipt["viewer"]["failures"]:
                    fail("the browser view disagreed with the API poses")
            if route:
                receipt["deleteStatus"] = (await client.delete(route)).status_code
                await package_recording(client, args.recordings_root, receipt, output)
            (output / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False))
    return receipt


async def package_recording(client, root, receipt, output):
    session = receipt["sessionId"]
    for _ in range(40):
        rows = [row for row in (await client.get("/api/v1/recordings")).json() if row["sessionId"] == session]
        if rows and rows[0]["status"] != "recording":
            break
        await asyncio.sleep(0.25)
    if not rows:
        receipt["recording"] = None
        return
    receipt["recording"] = {name: rows[0].get(name) for name in ("status", "sceneVersion", "durationMs")}
    if root:
        directory = root / session
        archive = output / f"recording-{session}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as file:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    file.write(path, Path(session) / path.relative_to(directory))
        receipt["recording"]["archive"] = archive.name


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--web", help="Web URL serving this API; adds the browser consistency observer")
    parser.add_argument(
        "--recordings-root", type=Path, help="The API's SPATIAL_RECORDINGS_ROOT; zips the recording"
    )
    parser.add_argument("--output", type=Path, default=ROOT / ".scratch/agent-journey")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=1800)
    result = asyncio.run(run(parser.parse_args()))
    summary = {
        "failures": result["failures"],
        "sessionId": result.get("sessionId"),
        "tools": len(result.get("agent", {}).get("tools", [])),
        "physicalChecks": len(result.get("physicalChecks", [])),
        "tickP95Ms": result.get("stats", {}).get("tickP95Ms"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    sys.exit(1 if result["failures"] else 0)
