"""Run independent TLS host + Gateway processes with disposable credentials and guaranteed owned cleanup."""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]


def stop_owned(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def wait_http(url, process):
    async with httpx.AsyncClient() as client, asyncio.timeout(30):
        while True:
            if process.poll() is not None:
                raise RuntimeError("Owned service exited before readiness; inspect its redacted log")
            try:
                response = await client.get(url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)


async def run(args):
    for port in (args.api_port, args.runner_port, *([args.web_port] if args.browser else [])):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    processes, logs = [], []
    api = f"http://127.0.0.1:{args.api_port}"
    receipt = {
        "source": args.source,
        "apiPort": args.api_port,
        "runnerPort": args.runner_port,
        "boundary": "independent CLI TLS host and Uvicorn Gateway processes on loopback",
        "browser": args.browser,
        "credentials": "generated for this run; values and private key excluded",
    }
    with tempfile.TemporaryDirectory(prefix="spatial-runner-") as temporary:
        cert, key = Path(temporary) / "cert.pem", Path(temporary) / "private.pem"
        openssl = shutil.which("openssl") or "C:/Program Files/Git/usr/bin/openssl.exe"
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
                "-out",
                str(cert),
                "-keyout",
                str(key),
            ],
            check=True,
            capture_output=True,
        )
        env = {
            **os.environ,
            "SPATIAL_RUNNER_TOKEN": secrets.token_urlsafe(32),
            "SPATIAL_RUNNER_CA_FILE": str(cert),
            "SPATIAL_RUNNER_HOST": "127.0.0.1",
            "SPATIAL_RUNNER_PORT": str(args.runner_port),
            "SPATIAL_RUNNER_SERVER_NAME": "localhost",
            "SPATIAL_RUNNER_KIND": args.source,
            "SPATIAL_REMOTE": "0",
            "SPATIAL_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.web_port}",
            "SPATIAL_API_URL": api,
            "SPATIAL_WEB_URL": f"http://127.0.0.1:{args.web_port}",
        }

        def launch(name, command, cwd=ROOT):
            log = (output.parent / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            processes.append(process)
            return process

        try:
            host = launch(
                "runner-cli",
                [
                    sys.executable,
                    "tools/run_remote_runner.py",
                    "--cert",
                    str(cert),
                    "--key",
                    str(key),
                    "--port",
                    str(args.runner_port),
                    "--source",
                    args.source,
                    "--device",
                    "cpu",
                ],
            )
            async with asyncio.timeout(10):
                while '"remote.runner.listening"' not in (output.parent / "runner-cli.log").read_text(
                    encoding="utf-8"
                ):
                    if host.poll() is not None:
                        raise RuntimeError("Runner CLI failed to start")
                    await asyncio.sleep(0.05)
            context = ssl.create_default_context(cafile=cert)
            _, tls_writer = await asyncio.open_connection(
                "127.0.0.1", args.runner_port, ssl=context, server_hostname="localhost"
            )
            receipt["tlsVersion"] = tls_writer.get_extra_info("ssl_object").version()
            tls_writer.close()
            await tls_writer.wait_closed()
            receipt["certificateSha256"] = hashlib.sha256(cert.read_bytes()).hexdigest()
            gateway = launch(
                "gateway-cli",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "apps.api.app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                    "--log-level",
                    "warning",
                ],
            )
            receipt["processes"] = {"client": os.getpid(), "runnerHost": host.pid, "gateway": gateway.pid}
            assert len(set(receipt["processes"].values())) == 3
            await wait_http(api + "/api/v1/health", gateway)
            async with httpx.AsyncClient(base_url=api, timeout=180) as http:
                created = await http.post(
                    "/api/v1/sessions",
                    json={
                        "sceneId": "lab-demo" if args.source == "synthetic" else "falling-boxes",
                        "source": args.source,
                    },
                )
                created.raise_for_status()
                sid = created.json()["sessionId"]
                route = f"/api/v1/sessions/{sid}"
                manifest = (await http.get(route + "/scene")).json()
                assert manifest["source"]["sampleTimeClock"] == "gateway-receive"
                async with connect(
                    api.replace("http:", "ws:") + route + "/ws", origin=f"http://127.0.0.1:{args.web_port}"
                ) as ws:
                    ready = json.loads(await ws.recv())
                    assert ready["sampleTimeClock"] == "gateway-receive"
                    await ws.send(
                        json.dumps({"type": "client.subscribe", "sceneRevision": manifest["sceneRevision"]})
                    )
                    async with asyncio.timeout(5):
                        while (snapshot := json.loads(await ws.recv()))["type"] != "stream.snapshot":
                            pass
                    samples = []
                    for command in ("pause", "step", "reset", "resume"):
                        await ws.send(
                            json.dumps({"type": "command.request", "requestId": command, "command": command})
                        )
                        async with asyncio.timeout(5):
                            while True:
                                value = json.loads(await ws.recv())
                                if (
                                    value["type"] == "session.state"
                                    and value["cause"].get("requestId") == command
                                ):
                                    assert value["state"] == ("RUNNING" if command == "resume" else "PAUSED")
                                    samples.append(
                                        {
                                            "command": command,
                                            "seq": value["seq"],
                                            "epoch": value["epoch"],
                                            "simTime": value["simTime"],
                                        }
                                    )
                                    break
                    receipt.update(
                        {
                            "bodies": len(snapshot["poses"]),
                            "sampleTimeClock": ready["sampleTimeClock"],
                            "commands": samples,
                        }
                    )
                assert (await http.delete(route)).status_code == 204
            if args.browser:
                node = shutil.which("node")
                web = launch(
                    "web-cli",
                    [
                        node,
                        str(ROOT / "apps/web/node_modules/vite/bin/vite.js"),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(args.web_port),
                    ],
                    cwd=ROOT / "apps/web",
                )
                await wait_http(env["SPATIAL_WEB_URL"], web)
                completed = await asyncio.to_thread(
                    subprocess.run,
                    [
                        node,
                        str(ROOT / "apps/web/node_modules/@playwright/test/cli.js"),
                        "test",
                        "binary.spec.ts",
                        "workbench.spec.ts",
                    ],
                    cwd=ROOT / "apps/web",
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
                (output.parent / "browser.log").write_text(
                    completed.stdout + completed.stderr, encoding="utf-8"
                )
                if completed.returncode:
                    raise RuntimeError("Remote browser journeys failed; inspect browser.log")
                receipt["browserTestsPassed"] = 2
            receipt["passed"] = True
        finally:
            try:
                async with httpx.AsyncClient(base_url=api, timeout=15) as http:
                    health = (await http.get("/api/v1/health")).json()
                    for session in health["sessions"]:
                        if session["state"] != "STOPPED":
                            await http.delete(f"/api/v1/sessions/{session['sessionId']}")
            except Exception:
                pass
            for process in reversed(processes):
                await asyncio.to_thread(stop_owned, process)
            for log in logs:
                log.close()
            receipt["ownedProcessesStopped"] = all(process.poll() is not None for process in processes)
    receipt["temporaryCredentialsRemoved"] = not Path(temporary).exists()
    receipt["savedAtEpochSeconds"] = time.time()
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=8008)
    parser.add_argument("--runner-port", type=int, default=8766)
    parser.add_argument("--web-port", type=int, default=5188)
    parser.add_argument("--source", choices=("synthetic", "newton"), default="synthetic")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".scratch/p2-d/cli-smoke.json"))
    asyncio.run(run(parser.parse_args()))
