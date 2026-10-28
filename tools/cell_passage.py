"""Build, import, launch and run an owned Cell Passage demonstration candidate.

This external client never writes poses or material, and its result is not a
substitute for the required three Newton runs and independent acceptance.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml

from apps.cli.lw.client import GatewayClient, GatewayError
from packages.library.common import content_hash
from tools.cell_passage_plan import compile_recipe, load_inputs
from tools.cell_passage_session import execute_round, prepare_round

ROOT = Path(__file__).resolve().parents[1]


def gateway_app():
    """A normal gateway with owned roots and an instance identity for safe startup."""
    from apps.api.app.main import create_app
    from apps.api.app.settings import Settings

    root = Path(os.environ["SPATIAL_PASSAGE_RUN_ROOT"]).resolve() / "runtime"
    nonce = os.environ["SPATIAL_PASSAGE_OWNER_NONCE"]
    os.environ["LW_LIBRARY"] = str(root / "library")
    settings = Settings(
        state_root=root / "state", recordings_root=root / "recordings", scene_previews_root=root / "previews",
        allowed_origins=tuple(os.environ["SPATIAL_ALLOWED_ORIGINS"].split(",")), node_trainer=(),
    )
    app = create_app(settings)

    @app.middleware("http")
    async def instance_identity(request, call_next):
        response = await call_next(request)
        response.headers["X-Passage-Owner"] = nonce
        return response

    return app


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf8")


def _available(port):
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", port))


def _asset_order(library):
    packages = {path.parent.relative_to(library / "assets").as_posix(): path.parent
                for path in sorted((library / "assets").rglob("asset.yaml"))}
    result, active, visited = [], set(), set()

    def visit(identity):
        if identity in visited:
            return
        if identity in active:
            raise ValueError("Generated asset dependency cycle: " + identity)
        active.add(identity)
        for path in packages[identity].rglob("*.usda"):
            for parts in re.findall(r"@library:assets/([^/@]+)/([^/@]+)/([1-9][0-9]*)/", path.read_text()):
                dependency = "/".join(parts)
                if dependency in packages and dependency != identity:
                    visit(dependency)
        active.remove(identity)
        visited.add(identity)
        result.append(packages[identity])

    for identity in packages:
        visit(identity)
    return result


async def _install(client, library, output):
    receipts = []
    for package in _asset_order(library):
        metadata = yaml.safe_load((package / "asset.yaml").read_text(encoding="utf8"))
        expected = metadata["id"] + "@" + package.name
        try:
            existing = await client.library_describe(expected)
        except GatewayError as error:
            if error.status_code != 404:
                raise
        else:
            if existing["contentHash"] != content_hash(package):
                raise ValueError("Fixed generated dependency conflicts with existing bytes: " + expected)
            receipts.append({"assetVersion": expected, "reused": True})
            continue
        receipt = await client.library_import(package)
        receipts.append(receipt)
        _write(output / "imports.json", receipts)
        if receipt["assetVersion"] != expected:
            raise ValueError("Fresh owned library assigned an unexpected dependency revision")
        if any(check["status"] == "fail" for check in receipt["validation"]["checks"]):
            raise ValueError("Generated dependency failed validation: " + expected)
    receipt = await client.library_import(library / "scenes/cell-passage/1")
    receipts.append(receipt)
    _write(output / "imports.json", receipts)
    report = await client.library_validate(receipt["sceneVersion"])
    _write(output / "validation.json", report)
    return receipt["sceneVersion"]


async def _claim(client, workcell, owner):
    values = (await client.get_state([workcell]))["entities"][workcell]["values"]
    await client.invoke_action(workcell, "requestControl", {
        "owner": values["controlOwner"], "epoch": values["controlEpoch"],
        "operationId": "claim-" + uuid.uuid4().hex, "nextOwner": owner, "mode": "auto",
    }, wait=False, actor="cli:cell-passage")
    for _ in range(600):
        values = (await client.get_state([workcell]))["entities"][workcell]["values"]
        if values["controlSafe"]:
            break
        await asyncio.sleep(0.1)
    else:
        raise ValueError("Source did not establish safe hold for automatic control")
    await client.invoke_action(workcell, "confirmControl", {
        "owner": values["controlOwner"], "epoch": values["controlEpoch"],
        "operationId": "confirm-" + uuid.uuid4().hex,
    }, wait=False, actor="cli:cell-passage")
    values = (await client.get_state([workcell]))["entities"][workcell]["values"]
    if values["controlOwner"] != owner or values["controlMode"] != "auto":
        raise ValueError("Source did not confirm the requested control owner")
    return values["controlEpoch"]


async def _prepare(args, library, bootstrap):
    url = f"http://127.0.0.1:{args.api_port}"
    async with GatewayClient(url=url) as client:
        identifier = await _install(client, library, args.out)
        session = await client.scene_launch(identifier, source="newton", seed=42)
    if "sessionId" not in session:
        raise ValueError("Owned fresh scene did not launch immediately; inspect its session request")
    bootstrap.update(session=session, sceneVersion=identifier, status="launched")
    _write(args.out / "bootstrap.json", bootstrap)
    async with GatewayClient(url=url, session=session["sessionId"]) as client:
        owner = "runner-" + args.round
        epoch = await _claim(client, "/World/Lab/LiquidHandler", owner)
        recipe = compile_recipe(*load_inputs(library), owner=owner, epoch=epoch, round_id=args.round)
        if not recipe["environmentChecksAvailable"]:
            raise ValueError("The generated package lacks the required environment checks")
        _write(args.out / "recipe.json", recipe)
        record = await prepare_round(client, recipe, args.out / "round")
        if not args.prepare_only:
            record = await execute_round(client, record, args.out / "round")
        return record


def demo(args):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.round):
        raise ValueError("A bounded round identity is required")
    if not 1 <= args.api_port <= 65535 or not 1 <= args.web_port <= 65535:
        raise ValueError("Ports must be in 1..65535")
    if not args.no_web and args.api_port == args.web_port:
        raise ValueError("API and viewer need different owned ports")
    _available(args.api_port)
    if not args.no_web:
        _available(args.web_port)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    library = args.generated.resolve() if args.generated else args.out / "generated"
    bootstrap = {"status": "building", "roundId": args.round, "experimentQualified": False}
    _write(args.out / "bootstrap.json", bootstrap)
    processes, logs = [], []
    nonce = uuid.uuid4().hex
    url = f"http://127.0.0.1:{args.api_port}"
    env = os.environ | {
        "SPATIAL_PASSAGE_RUN_ROOT": str(args.out), "SPATIAL_PASSAGE_OWNER_NONCE": nonce,
        "LW_LIBRARY": str(args.out / "runtime/library"),
        "SPATIAL_STATE_ROOT": str(args.out / "runtime/state"),
        "SPATIAL_RECORDINGS_ROOT": str(args.out / "runtime/recordings"),
        "SPATIAL_SCENE_PREVIEWS_ROOT": str(args.out / "runtime/previews"),
        "SPATIAL_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.web_port}",
        "SPATIAL_API_URL": url, "SPATIAL_NEWTON_DEVICE": "cpu", "SPATIAL_RECORDINGS_ENABLED": "1",
        "SPATIAL_NODE_TRAINER": "[]",
    }
    try:
        if args.source:
            for command in (
                [sys.executable, "-m", "factory.cell_passage", "--source", str(args.source.resolve()),
                 "--out", str(library), "--with-workcell"],
                [sys.executable, "-m", "factory.cell_passage_teach", "--library", str(library)],
            ):
                subprocess.run(command, cwd=ROOT, env=env, check=True)
        inputs = load_inputs(library)
        candidate = compile_recipe(*inputs, owner="auto", epoch=0, round_id=args.round)
        if not candidate["environmentChecksAvailable"]:
            raise ValueError("Generated scene is missing the environment/managed-device configuration")
        _available(args.api_port)
        commands = [([sys.executable, "-m", "uvicorn", "--factory", "tools.cell_passage:gateway_app",
                     "--host", "127.0.0.1", "--port", str(args.api_port)], "api")]
        if not args.no_web:
            _available(args.web_port)
            node = shutil.which("node")
            if node is None:
                raise ValueError("Node is required for the owned viewer; use --no-web for a CLI-only run")
            commands.append(([
                node, str(ROOT / "apps/web/node_modules/vite/bin/vite.js"),
                str(ROOT / "apps/web"), "--host", "127.0.0.1", "--port", str(args.web_port),
            ], "web"))
        for command, name in commands:
            log = (args.out / f"{name}.log").open("w", encoding="utf8")
            logs.append(log)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                               stderr=subprocess.STDOUT, creationflags=flags))
        for _ in range(240):
            if any(process.poll() is not None for process in processes):
                raise ValueError("An owned service exited; inspect this run's logs")
            try:
                response = httpx.get(url + "/api/v1/health", timeout=1, trust_env=False)
                response.raise_for_status()
                if response.headers.get("X-Passage-Owner") == nonce:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        else:
            raise ValueError("Owned API readiness/identity could not be confirmed")
        bootstrap["status"] = "preparing"
        _write(args.out / "bootstrap.json", bootstrap)
        result = asyncio.run(_prepare(args, library, bootstrap))
        bootstrap.update(status=result["status"], experimentQualified=False)
        _write(args.out / "bootstrap.json", bootstrap)
        print(json.dumps({"status": result["status"], "session": bootstrap["session"]["sessionId"],
                          "viewer": f"http://127.0.0.1:{args.web_port}/sessions/{bootstrap['session']['sessionId']}",
                          "evidence": str(args.out), "experimentQualified": False}), flush=True)
        if not args.exit_after_run:
            while all(process.poll() is None for process in processes):
                time.sleep(0.5)
        return 0 if result["status"] in ("plans_completed", "prepared") else 3
    except KeyboardInterrupt:
        if bootstrap["status"] == "plans_completed":
            bootstrap["viewerClosed"] = True
            return 0
        bootstrap["status"] = "interrupted"
        _write(args.out / "bootstrap.json", bootstrap)
        raise
    except BaseException:
        bootstrap["status"] = "interrupted"
        _write(args.out / "bootstrap.json", bootstrap)
        raise
    finally:
        if bootstrap.get("session"):
            try:
                response = httpx.get(url + "/api/v1/health", timeout=2, trust_env=False)
                if response.headers.get("X-Passage-Owner") == nonce:
                    httpx.delete(url + "/api/v1/sessions/" + bootstrap["session"]["sessionId"],
                                 timeout=30, trust_env=False).raise_for_status()
            except httpx.HTTPError:
                bootstrap["cleanup"] = "session_close_unconfirmed"
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        _write(args.out / "bootstrap.json", bootstrap)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="Read-only assets02 platform_packages source")
    source.add_argument("--generated", type=Path, help="Already built/taught package root")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--round", default=uuid.uuid4().hex[:12])
    parser.add_argument("--api-port", type=int, default=8023)
    parser.add_argument("--web-port", type=int, default=5223)
    parser.add_argument("--no-web", action="store_true")
    parser.add_argument("--prepare-only", action="store_true", help="Publish Plans for the browser client")
    parser.add_argument("--exit-after-run", action="store_true", help="Close owned services after the run")
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(demo(args))
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
