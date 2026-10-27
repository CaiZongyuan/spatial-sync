"""An independently hosted catalog completes CLI maintenance with Lab imports forbidden."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from cli_support import LW, ROOT

from tests.fixtures.catalog_source import REVISION, CatalogSourceFixture

BASE = "/api/v1/library/catalog/maintenance"


def test_independent_catalog_runs_its_worker_and_cli_without_lab_imports(tmp_path):
    fixture = CatalogSourceFixture()

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            response = fixture.handle(httpx.Request("GET", "http://" + self.headers["Host"] + self.path))
            self.send_response(response.status_code)
            for key, value in response.headers.items():
                if key.lower() == "link":
                    value = value.replace("https://huggingface.co", "http://" + self.headers["Host"])
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.content)

    blocker = tmp_path / "import-guard"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(
        "import importlib.abc,sys\n"
        "class CatalogOnly(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self,name,path=None,target=None):\n"
        "  if name.split('.')[0] in {'apps','newton','warp','trimesh','pxr','onnxruntime','torch','mcp'} "
        "or (name.startswith('packages.') and not name.startswith('packages.catalog_metadata')):\n"
        "   raise ImportError('Forbidden standalone catalog dependency: '+name)\n"
        "sys.meta_path.insert(0,CatalogOnly())\n",
        encoding="utf-8",
    )
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = os.environ | {
        "LW_CATALOG_STATE_ROOT": str(tmp_path / "catalog"),
        "LW_CATALOG_OPERATOR_TOKEN": "standalone-fixture-only",
        "LW_CATALOG_HF_ENDPOINT": "http://127.0.0.1:" + str(upstream.server_port),
        "PYTHONPATH": str(blocker) + os.pathsep + str(ROOT),
        "PYTHONIOENCODING": "utf-8",
    }
    environment.pop("LW_CATALOG_AGENT_PROFILE", None)
    controller = environment | {
        "PYTHONPATH": str(ROOT / "apps/cli") + os.pathsep + str(ROOT),
        "LW_URL": "http://127.0.0.1:" + str(port),
        "LW_TOKEN": "standalone-fixture-only",
    }

    def cli(*arguments):
        result = subprocess.run(
            [str(LW), *arguments, "--json"],
            env=controller,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    with (tmp_path / "catalog.log").open("wb") as log:
        process = subprocess.Popen(
            [
                os.getenv("LW_CATALOG_TEST_PYTHON", sys.executable),
                "-m",
                "packages.catalog_metadata",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            with httpx.Client(
                base_url=controller["LW_URL"],
                headers={
                    "Authorization": "Bearer standalone-fixture-only",
                },
                timeout=10,
                trust_env=False,
            ) as http:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, (tmp_path / "catalog.log").read_text()
                    try:
                        if http.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.03)
                else:
                    raise AssertionError("Standalone catalog did not start")
                assert cli("catalog", "collections")["collections"] and not fixture.requests
                assert http.patch(BASE + "/collections/simready", json={"enabled": True}).status_code == 200
                run = cli(
                    "catalog",
                    "maintain",
                    "simready",
                    "--revision",
                    REVISION,
                    "--organizer",
                    "external",
                    "--previews",
                    "--wait",
                )
                assert run["phase"] == "awaiting_agent" and run["previewCoverage"]["available"] == 2, run
                file = tmp_path / "receipt.json"
                while True:
                    batch = cli("catalog", "batch-claim", run["id"], "--limit", "1")
                    if not batch["entries"]:
                        break
                    receipt = {
                        "inputKey": batch["inputKey"],
                        "usage": {"totalTokens": None},
                        "nativeSessionId": None,
                        "results": [
                            {
                                "upstreamId": item["upstreamId"],
                                "inputDigest": item["inputDigest"],
                                "fields": {"summary": {"zh": "实验室存储容器的确定性测试描述"}},
                            }
                            for item in batch["entries"]
                        ],
                    }
                    file.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
                    cli("catalog", "batch-submit", run["id"], batch["id"], str(file))
                final = cli("catalog", "maintenance-finish", run["id"])
                assert final["status"] == "succeeded"
                assert final["usage"]["modelTokens"] is None and final["modelPolicy"] == "external"
                assert final["usage"]["externalBatches"] == 2
                found = cli("catalog", "metadata-search", "--q", "实验室存储")
                assert found["total"] == 2
                before = list(fixture.requests)
                output = tmp_path / "object.webp"
                cli("catalog", "thumbnail", found["results"][0]["id"], "--output", str(output))
                assert output.read_bytes()[8:12] == b"WEBP" and fixture.requests == before
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=5)
