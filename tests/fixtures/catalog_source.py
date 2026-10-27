"""Deterministic upstream HTTP, shared by public gateway, CLI and browser journeys."""

import base64
import threading
import time
from io import BytesIO
from urllib.parse import unquote

import httpx
from PIL import Image

from packages.catalog.source import SimReadySource

REVISION = "524747d9b5e1f21b5b18b56041788c01d745f1a1"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
)
PNG_PIXELS = ((1, 1), (255, 255, 255, 255))


def thumbnail_pixels(data):
    with Image.open(BytesIO(data)) as image:
        assert image.format == "WEBP"
        return image.size, image.convert("RGBA").getpixel((0, 0))


class CatalogSourceFixture:
    def __init__(self):
        self.revision = REVISION
        self.names = ["CT_fixture_a01", "box_cardboard_a01"]
        self.requests = []
        self.fail_listing = False
        self.broken_metadata = set()
        self.delay = 0
        self.unsafe_next = False
        self.entered = threading.Event()
        self.images = {}

    def source(self, **kwargs):
        return SimReadySource(client=httpx.Client(transport=httpx.MockTransport(self.handle)), **kwargs)

    def handle(self, request):
        path = unquote(request.url.path)
        self.requests.append(path)
        if "/revision/main" in path:
            return httpx.Response(200, json={"sha": self.revision})
        root = "/api/datasets/nvidia/simready-assets/tree/" + self.revision
        if path == root:
            self.entered.set()
            if self.delay:
                time.sleep(self.delay)
            if self.fail_listing:
                return httpx.Response(410)
            start = int(request.url.params.get("cursor", 0))
            names = self.names[start:start + 1]
            headers = {}
            if start + 1 < len(self.names):
                headers["Link"] = '<https://huggingface.co' + root + f'?cursor={start + 1}>; rel="next"'
            if self.unsafe_next:
                headers["Link"] = '<https://unexpected.invalid/private>; rel="next"'
            return httpx.Response(200, json=[{
                "type": "directory", "path": name, "oid": f"{index + 1:040x}",
            } for index, name in enumerate(names, start=start)], headers=headers)
        if path.startswith(root + "/"):
            package = path.removeprefix(root + "/")
            self.entered.set()
            if self.delay:
                time.sleep(self.delay)
            if package in self.broken_metadata:
                return httpx.Response(404)
            return httpx.Response(200, json=[
                {"type": "file", "path": package + "/com.nvidia.simready.packaging.json", "size": 25},
                {"type": "file", "path": package + "/simready_usd/model.usd", "size": 9000000},
                {"type": "file", "path": package + "/simready_usd/.thumbs/256x256/large.png", "size": 900000},
                {"type": "file", "path": package + "/simready_usd/.thumbs/small.png",
                 "size": len(self.images.get(package, PNG))},
            ])
        if "/resolve/" in path and path.endswith("/com.nvidia.simready.packaging.json"):
            return httpx.Response(200, json={"license": "CC-BY-4.0"})
        if "/resolve/" in path and path.endswith("/.thumbs/small.png"):
            package = path.partition("/resolve/")[2].split("/")[1]
            return httpx.Response(
                200, content=self.images.get(package, PNG), headers={"Content-Type": "image/png"}
            )
        raise AssertionError("Unexpected upstream request (models and large previews forbidden): " + path)
