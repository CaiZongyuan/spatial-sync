"""Run the I5 journey: Claude Code completes the I3 flow through `lw mcp` alone.

One command starts an owned Newton-capable API and web server on fresh roots, then
runs Claude Code headless in an empty directory with no built-in tools, so the lab
MCP server is its only interface. While the agent works, an HTTP observer checks
every settled tube Site location against physics with the I3 oracle and a browser
observer compares the live viewer with the API. Afterwards the runner checks the
invocation log, closes the session, packages the recording and stops what it started.
"""

import argparse
import asyncio
import contextlib
import json
import os
import signal
import socket
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.protocol.schema import SceneManifest, TransformFrame
from tools.smoke_info_newton import CENTRIFUGE, ROBOT, physical_check

TUBES = {f"/World/Lab/Tubes/Tube{index:02d}": index for index in (1, 2, 3, 4)}
FINAL = {key: f"/World/Lab/Rack/A{index}" for key, index in TUBES.items()}
LIVE = {"CREATED", "LOADING", "READY", "RUNNING", "PAUSED", "STOPPING"}
AGENT = {"kind": "agent", "name": "claude-code"}
PROMPT = """你在操作一个物理仿真实验室，只能使用 lab MCP 工具。请完成这个实验：

1. 在场景库里找到 robot-and-tubes，用 Newton 物理源启动。
2. 把架子 A1 的试管放进离心机 1 号孔位，把 A2 的试管放进 3 号孔位。
3. 关上离心机盖，以 3000 rpm 离心 20 秒，等离心完成。
4. 打开盖子，把两支试管分别放回架子 A1 和 A2。

动作被拒绝时，读拒绝原因和提示，自行调整后继续。完成后用两三句话总结你做了什么、遇到了哪些拒绝。"""


def step(key, action, params=None):
    return (key, action, params or {})


def pick_place_step(source, target):
    return step(ROBOT, "pickPlace", {"from": source, "to": target})


def journey_order(invocations):
    """Follow I3's causal order through the succeeded actions; rejections may appear anywhere."""
    done = [
        step(item["entityKey"], item["action"], item["params"])
        for item in invocations
        if item["status"] == "succeeded"
    ]

    def after(expected, index):
        if index is None:
            return None
        return next((i for i, item in enumerate(done) if i > index and item == expected), None)

    steps = {
        "slot1": after(pick_place_step("rack.A1", "centrifuge.slot1"), -1),
        "slot3": after(pick_place_step("rack.A2", "centrifuge.slot3"), -1),
    }
    placed = None if None in steps.values() else max(steps.values())
    steps["closeLid"] = after(step(CENTRIFUGE, "closeLid"), placed)
    steps["start"] = after(step(CENTRIFUGE, "start", {"rpm": 3000, "durationSeconds": 20}), steps["closeLid"])
    steps["openLid"] = after(step(CENTRIFUGE, "openLid"), steps["start"])
    steps["rackA1"] = after(pick_place_step("centrifuge.slot1", "rack.A1"), steps["openLid"])
    steps["rackA2"] = after(pick_place_step("centrifuge.slot3", "rack.A2"), steps["openLid"])
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


class Owned:
    """Processes this run started, each in its own process group so cleanup reaches its children."""

    def __init__(self):
        self.processes = {}

    async def start(self, name, *command, **options):
        process = await asyncio.create_subprocess_exec(*command, start_new_session=True, **options)
        self.processes[name] = process
        return process

    async def stop(self, name, grace=15, leader_first=False):
        """Terminate the group, or the leader first when it must finish its own shutdown."""
        process = self.processes[name]
        if leader_first and process.returncode is None:
            process.send_signal(signal.SIGTERM)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), grace)
        gone = False
        for sent in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sent)
            except ProcessLookupError:
                gone = True
                break
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    gone = True
                    break
                await asyncio.sleep(0.1)
            else:
                continue
            break
        if not gone:
            raise RuntimeError(f"owned process group {name} (PID {process.pid}) did not stop")
        await process.wait()
        del self.processes[name]
        return process.returncode

    async def stop_all(self):
        errors = []
        for name, process in list(self.processes.items()):
            try:
                await self.stop(name)
            except Exception as error:
                errors.append({"name": name, "pid": process.pid, "error": repr(error)})
        return errors


def free_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


async def sessions(client):
    return (await client.get("/api/v1/health")).json()["sessions"]


async def wait_for_session(client, agent):
    """The agent launches the scene itself; None means it exited without a session."""
    while True:
        if ready := [item for item in await sessions(client) if item["state"] in {"RUNNING", "PAUSED"}]:
            return ready[0]["sessionId"]
        if agent.done():
            return None
        await asyncio.sleep(0.25)


def viewer_confirmed(report, session):
    return bool(
        report
        and report["sessionId"] == session
        and not report["failures"]
        and report["locationComparisons"]
        and all(report.get("poseComparisonsByTube", {}).get(key, 0) > 0 for key in TUBES)
    )


async def run_agent(owned, command, cwd, output, timeout):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CLAUDECODE")}
    tools, result = [], None
    with (output / "agent-stderr.log").open("wb") as stderr:
        # One stream-json line carries a whole tool result, e.g. a scene description.
        process = await owned.start(
            "agent",
            *command,
            cwd=cwd,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            limit=64 * 1024 * 1024,
        )
        try:
            async with asyncio.timeout(timeout):
                with (output / "transcript.jsonl").open("wb") as file:
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
                                    shown = {"tool": item["name"], "input": item["input"]}
                                    print(json.dumps(shown, ensure_ascii=False), flush=True)
                        elif message.get("type") == "result":
                            result = message
                await process.wait()
        finally:
            # Also stops the lab MCP server the agent spawned.
            await owned.stop("agent")
    return {"exitCode": process.returncode, "tools": tools, "result": result}


class Run:
    def __init__(self, args):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.args, self.output = args, (args.output / stamp).resolve()
        self.lab, self.owned, self.stop = self.output / "lab", Owned(), asyncio.Event()
        self.receipt = {"output": str(self.output), "prompt": PROMPT, "failures": []}
        self.client = self.agent = self.watcher = self.observer = self.session = None

    def fail(self, reason):
        self.receipt["failures"].append(reason)

    async def execute(self):
        try:
            await self.start_lab()
            await self.journey()
        except Exception as error:
            self.fail(f"runner error: {error!r}")
        finally:
            try:
                await self.cleanup()
            except Exception as error:
                self.fail(f"cleanup error: {error!r}")
            try:
                errors = await self.owned.stop_all()
                if errors:
                    self.receipt["cleanupErrors"] = errors
                    for error in errors:
                        self.fail(f"stopping {error['name']} (PID {error['pid']}): {error['error']}")
            except Exception as error:
                self.fail(f"process cleanup error: {error!r}")
            try:
                if self.client:
                    await self.client.aclose()
            except Exception as error:
                self.fail(f"HTTP cleanup error: {error!r}")
            finally:
                if self.owned.processes:
                    self.receipt["remainingProcesses"] = [
                        {"name": name, "pid": process.pid} for name, process in self.owned.processes.items()
                    ]
                    self.fail("owned process groups remain; see remainingProcesses")
                self.output.mkdir(parents=True, exist_ok=True)
                (self.output / "receipt.json").write_text(
                    json.dumps(self.receipt, indent=2, ensure_ascii=False)
                )
        return self.receipt

    async def start_lab(self):
        roots = {
            "LW_LIBRARY": "library",
            "SPATIAL_STATE_ROOT": "state",
            "SPATIAL_RECORDINGS_ROOT": "recordings",
        }
        for name in roots.values():
            (self.lab / name).mkdir(parents=True)
        api_port, web_port = free_port(), free_port()
        self.api, self.web = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{web_port}"
        self.receipt |= {"api": self.api, "web": self.web}
        environment = os.environ | {key: str(self.lab / name) for key, name in roots.items()}
        api = [sys.executable, "-m", "uvicorn", "apps.api.app.main:app", "--host", "127.0.0.1"]
        with (self.output / "api.log").open("wb") as log:
            await self.owned.start(
                "api",
                *api,
                "--port",
                str(api_port),
                cwd=ROOT,
                env=environment | {"SPATIAL_ALLOWED_ORIGINS": self.web},
                stdout=log,
                stderr=log,
            )
        with (self.output / "web.log").open("wb") as log:
            web = ["pnpm", "exec", "vite", "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"]
            await self.owned.start(
                "web",
                *web,
                cwd=ROOT / "apps/web",
                env=os.environ | {"SPATIAL_API_URL": self.api},
                stdout=log,
                stderr=log,
            )
        self.client = httpx.AsyncClient(base_url=self.api, timeout=60, trust_env=False)
        for name, url in (("api", self.api + "/api/v1/health"), ("web", self.web)):
            deadline = time.monotonic() + 90
            while True:
                if self.owned.processes[name].returncode is not None:
                    raise RuntimeError(f"The {name} server exited during startup; see {name}.log")
                with contextlib.suppress(httpx.TransportError):
                    if (await self.client.get(url)).status_code == 200:
                        break
                if time.monotonic() > deadline:
                    raise TimeoutError(f"The {name} server did not become ready")
                await asyncio.sleep(0.5)

    async def journey(self):
        client, args = self.client, self.args
        config = self.output / "mcp.json"
        lab = {"command": "uv", "args": ["--directory", str(ROOT), "run", "--no-sync", "lw", "mcp"]}
        config.write_text(json.dumps({"mcpServers": {"lab": lab | {"env": {"LW_URL": self.api}}}}, indent=2))
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
        self.receipt["command"] = command[:2] + ["<prompt>"] + command[3:]
        with tempfile.TemporaryDirectory(prefix="lw-agent-") as cwd:
            self.agent = asyncio.create_task(run_agent(self.owned, command, cwd, self.output, args.timeout))
            self.session = await wait_for_session(client, self.agent)
            if self.session:
                route = f"/api/v1/sessions/{self.session}"
                manifest = SceneManifest.model_validate((await client.get(route + "/scene")).json())
                self.observer = Observer(client, route, manifest)
                self.watcher = asyncio.create_task(self.observer.watch(self.stop))
                viewer = ["tools/agent_journey_viewer.ts", "--web", self.web, "--api", self.api]
                await self.owned.start(
                    "viewer",
                    str(ROOT / "node_modules/.bin/tsx"),
                    *viewer,
                    "--session",
                    self.session,
                    "--output",
                    str(self.output / "viewer"),
                    cwd=ROOT,
                )
            self.receipt["agent"] = await self.agent
        if not self.session:
            self.fail("the agent did not launch a session")
            return
        self.receipt["sessionId"] = self.session
        self.stop.set()
        await self.watcher
        for key, expected in FINAL.items():
            if not await self.observer.check(key, expected):
                self.fail(f"the final location of {key} is not {expected}")
        observer = self.observer
        self.receipt |= {"locationEvents": observer.events, "physicalChecks": observer.checks}
        self.receipt["looseEvents"] = [event for event in observer.events if event["location"] == "loose"]
        untouched = {"/World/Lab/Tubes/Tube03", "/World/Lab/Tubes/Tube04"}
        if any(
            event["key"] in untouched and event["location"] != FINAL[event["key"]]
            for event in observer.events
        ):
            self.fail("a tube outside the journey left its rack slot")
        self.receipt["centrifugeSamples"] = observer.centrifuge
        stats = (await client.get(route + "/stats")).json()
        self.receipt["stats"] = stats
        if (stats["source"], stats["sceneId"], stats["device"]) != ("newton", "robot-and-tubes", "cpu"):
            self.fail("the session was not Newton robot-and-tubes on CPU")
        invocations = (await client.get(route + "/invocations")).json()["invocations"]
        invocations.sort(key=lambda item: item["createdAtMs"])
        fields = ("entityKey", "action", "params", "status", "error", "actor")
        self.receipt["invocations"] = [{name: item.get(name) for name in fields} for item in invocations]
        self.receipt["order"] = journey_order(invocations)
        if not self.receipt["order"]["ordered"]:
            self.fail("the succeeded actions do not complete the I3 order")
        if any(item["actor"] != AGENT for item in invocations):
            self.fail("an invocation was not made by agent:claude-code")
        tools = self.receipt["agent"]["tools"]
        if not tools or not all(name.startswith("mcp__lab__") for name in tools):
            self.fail("the agent used a tool other than lab MCP")
        if len(invocations) > tools.count("mcp__lab__invoke_action"):
            self.fail("the invocation log has actions the agent did not request")
        if any(not check["passed"] for check in observer.checks):
            self.fail("a settled tube location disagreed with physics")
        if self.receipt["agent"]["exitCode"] != 0:
            self.fail("Claude Code exited with an error")

    async def cleanup(self):
        self.stop.set()
        tasks = [task for task in (self.agent, self.watcher) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if "viewer" in self.owned.processes:
            # Leader first: the viewer takes its final screenshot before closing its own browser.
            self.receipt["viewerExitCode"] = code = await self.owned.stop(
                "viewer", grace=60, leader_first=True
            )
            path = self.output / "viewer/viewer.json"
            report = json.loads(path.read_text()) if path.exists() else None
            self.receipt["viewer"] = report
            if code != 0 or not viewer_confirmed(report, self.session):
                self.fail("the browser observer did not confirm the API state")
        if not self.client:
            return
        created = await sessions(self.client)
        self.receipt["sessions"] = [item["sessionId"] for item in created]
        if len(created) != 1:
            self.fail(f"the agent created {len(created)} sessions instead of one")
        for item in created:
            if item["state"] in LIVE:
                status = (await self.client.delete(f"/api/v1/sessions/{item['sessionId']}")).status_code
                self.receipt.setdefault("deleteStatus", {})[item["sessionId"]] = status
                if status != 204:
                    self.fail(f"closing {item['sessionId']} returned {status}")
        if self.session:
            await self.package_recording()

    async def package_recording(self):
        for _ in range(40):
            rows = [
                row
                for row in (await self.client.get("/api/v1/recordings")).json()
                if row["sessionId"] == self.session
            ]
            if rows and rows[0]["status"] != "recording":
                break
            await asyncio.sleep(0.25)
        recording = rows[0] if rows else None
        self.receipt["recording"] = recording and {
            name: recording.get(name) for name in ("status", "sceneVersion", "durationMs")
        }
        if not recording or recording["status"] != "complete":
            self.fail("the recording did not complete")
            return
        directory = self.lab / "recordings" / self.session
        files = [path for path in sorted(directory.rglob("*")) if path.is_file()]
        if not files:
            self.fail("the recording directory is empty")
            return
        archive = self.output / f"recording-{self.session}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as file:
            for path in files:
                file.write(path, Path(self.session) / path.relative_to(directory))
        self.receipt["recording"] |= {"archive": archive.name, "files": len(files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--output", type=Path, default=ROOT / ".scratch/agent-journey")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=1800)
    result = asyncio.run(Run(parser.parse_args()).execute())
    summary = {
        "output": result["output"],
        "failures": result["failures"],
        "sessionId": result.get("sessionId"),
        "tools": len(result.get("agent", {}).get("tools", [])),
        "physicalChecks": len(result.get("physicalChecks", [])),
        "looseEvents": len(result.get("looseEvents", [])),
        "tickP95Ms": result.get("stats", {}).get("tickP95Ms"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    sys.exit(1 if result["failures"] else 0)
