"""Recording ownership, portable scene assets and read-only HTTP discovery."""

import asyncio
import json
import re
import shutil
import struct
from hashlib import sha256
from pathlib import Path, PureWindowsPath

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from packages.assets.registry import asset_id_from_url
from packages.info.log import SessionLog, recover_recordings

RECORDING_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}$")


def _standalone_gltf(payload, suffix):
    if suffix == ".glb":
        if len(payload) < 20 or payload[:4] != b"glTF":
            raise ValueError("Invalid recording GLB asset")
        length, kind = struct.unpack_from("<II", payload, 12)
        if kind != 0x4E4F534A:
            raise ValueError("GLB must begin with its JSON chunk")
        data = json.loads(payload[20 : 20 + length])
    elif suffix == ".gltf":
        data = json.loads(payload)
    else:
        return
    for item in [*data.get("buffers", []), *data.get("images", [])]:
        uri = item.get("uri", "")
        if uri and not uri.startswith("data:"):
            raise ValueError("Recording capture requires self-contained GLTF buffers and images")


async def freeze_manifest(manifest, source, registry):
    """Capture every render/fallback/collider asset before its Runner can close."""
    value = manifest.model_dump(mode="json")
    assets, urls = {}, {}
    for node in value["nodes"]:
        for group in ("visuals", "fallbackVisuals", "colliders"):
            for visual in node.get(group) or []:
                url = visual.get("url")
                if not url:
                    continue
                if url in urls:
                    visual["url"] = urls[url]
                    continue
                suffix = Path(url).suffix.lower() or ".glb"
                if visual["kind"] == "mesh":
                    payload = await source.read_mesh(visual["meshId"])
                    suffix = ".glb"
                else:
                    asset_id = asset_id_from_url(url)
                    try:
                        asset = await asyncio.to_thread(registry.read, asset_id)
                        payload = asset.data
                    except KeyError:
                        # Preserve the intentional missing-primary fallback, locally.
                        visual["url"] = urls[url] = (
                            "assets/missing-" + sha256(url.encode()).hexdigest() + suffix
                        )
                        continue
                _standalone_gltf(payload, suffix)
                name = "assets/sha256-" + sha256(payload).hexdigest() + suffix
                assets[name] = payload
                visual["url"] = urls[url] = name
    return value, assets


class RecordingRepository:
    def __init__(self, settings, asset_registry):
        self.root = Path(settings.recordings_root).resolve()
        self.keep = settings.recordings_keep
        self.enabled = settings.recordings_enabled
        self.asset_registry = asset_registry

    async def startup(self):
        await asyncio.to_thread(recover_recordings, self.root)
        await asyncio.to_thread(self.retain)

    def list(self):
        values = []
        if not self.root.exists():
            return values
        for directory in self.root.iterdir():
            if not directory.is_dir() or directory.is_symlink() or not RECORDING_ID.fullmatch(directory.name):
                continue
            try:
                value = json.loads((directory / "recording.json").read_bytes())
                if value.get("sessionId") != directory.name or value.get("formatVersion") != 1:
                    continue
                value["url"] = f"/recordings/{directory.name}/recording.json"
                values.append(value)
            except (OSError, ValueError):
                continue
        return sorted(values, key=lambda value: value.get("createdAt", ""), reverse=True)

    def retain(self):
        values = [item for item in self.list() if item.get("status") in ("complete", "incomplete")]
        for value in values[self.keep :]:
            directory = self.root / value["sessionId"]
            # Validate the final absolute deletion target; never follow a directory link.
            if directory.is_symlink() or directory.resolve().parent != self.root:
                continue
            shutil.rmtree(directory)

    def create(self, session_id, scene_id):
        return SessionLog(self.root / session_id, session_id, scene_id) if self.enabled else None

    def resolve(self, recording_id, relative):
        if not RECORDING_ID.fullmatch(recording_id) or "\\" in relative or PureWindowsPath(relative).drive:
            raise HTTPException(404, {"code": "recording_missing"})
        directory = self.root / recording_id
        path = directory / relative
        if (
            directory.is_symlink()
            or not path.resolve().is_relative_to(directory.resolve())
            or not path.is_file()
        ):
            raise HTTPException(404, {"code": "recording_missing"})
        return path


def install_recording_routes(app, repository):
    @app.get("/api/v1/recordings")
    async def recordings():
        return await asyncio.to_thread(repository.list)

    @app.get("/recordings/{recording_id}/{relative:path}")
    async def recording_file(recording_id: str, relative: str):
        path = await asyncio.to_thread(repository.resolve, recording_id, relative)
        if path.suffix == ".json":
            # Close metadata read handles before the network send, allowing the
            # writer's atomic replacement while clients browse active recordings.
            payload = await asyncio.to_thread(path.read_bytes)
            return Response(payload, media_type="application/json", headers={"Cache-Control": "no-cache"})
        return FileResponse(path, headers={"Cache-Control": "no-cache"})
