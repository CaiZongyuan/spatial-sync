"""Owned production worker/HTTP with a deterministic HF-shaped loopback source."""

import json
import os
import threading
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from fastapi import Request

from apps.api.app.main import create_app

REVISION = "a" * 40
REQUESTS = []
CONTROL = {"delay": 0, "fail": False}


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        REQUESTS.append(path)
        if CONTROL["delay"]:
            time.sleep(CONTROL["delay"])
        prefix = "/api/datasets/nvidia/simready-assets/tree/" + REVISION
        resolve = "/datasets/nvidia/simready-assets/resolve/" + REVISION
        if CONTROL["fail"]:
            self.send_error(503)
            return
        if path == prefix:
            value = [
                {"type": "directory", "path": name, "oid": ("b" if index == 0 else "c") * 40}
                for index, name in enumerate(("beaker", "bucket"))
            ]
        elif path in (prefix + "/beaker", prefix + "/bucket"):
            name = path.rsplit("/", 1)[-1]
            value = [
                {"type": "file", "path": name + "/" + filename, "size": size, "oid": "d" * 40}
                for filename, size in (
                    ("com.nvidia.simready.packaging.json", 200),
                    (".thumbs/preview.png", 100),
                    (".thumbs/normal.png", 1),
                    ("simready_usd/model.usd", 1000),
                )
            ]
        elif path.startswith(resolve) and path.endswith("com.nvidia.simready.packaging.json"):
            name = path.split("/")[-2]
            value = {"name": name, "description": "A publisher-described laboratory container."}
        elif path.endswith("/.thumbs/preview.png"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            from tests.fixtures.catalog_source import PNG

            self.wfile.write(PNG)
            return
        else:
            self.send_error(404)
            return
        data = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
thread = threading.Thread(target=upstream.serve_forever, daemon=True)
thread.start()
os.environ["LW_CATALOG_HF_ENDPOINT"] = f"http://127.0.0.1:{upstream.server_port}"
app = create_app()
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def owned_lifespan(application):
    try:
        async with original_lifespan(application):
            yield
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)


app.router.lifespan_context = owned_lifespan


@app.get("/e2e/catalog-metadata-source")
async def source_requests():
    return {"requests": list(REQUESTS), "control": CONTROL}


@app.post("/e2e/catalog-metadata-source")
async def source_control(request: Request):
    value = await request.json()
    CONTROL["delay"] = min(2, max(0, float(value.get("delay", 0))))
    CONTROL["fail"] = value.get("fail", False) is True
    if value.get("clear"):
        REQUESTS.clear()
    return CONTROL


@app.post("/e2e/catalog-metadata-worker/{identifier}/crash")
async def crash_worker(identifier: str):
    process = app.state.metadata_maintenance._processes.get(identifier)
    if process is None or process.returncode is not None:
        return {"killed": False}
    process.kill()
    await process.wait()
    return {"killed": True}
