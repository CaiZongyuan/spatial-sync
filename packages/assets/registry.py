"""Read only explicitly registered repository assets; request IDs never become paths."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote

import yaml

ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets"
MEDIA_TYPES = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".bin": "application/octet-stream",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def asset_id_from_url(url: str) -> str:
    prefix = "/api/v1/assets/"
    if not url.startswith(prefix):
        raise ValueError("binding URL must reference a registered /api/v1/assets/ ID")
    asset_id = unquote(url[len(prefix) :])
    if (
        not asset_id
        or any(x in asset_id for x in "\\?#")
        or any(part in ("", ".", "..") for part in asset_id.split("/"))
    ):
        raise ValueError("invalid asset ID")
    return asset_id


@dataclass(frozen=True)
class Asset:
    data: bytes
    media_type: str
    etag: str


class AssetRegistry:
    def __init__(self, paths: dict[str, str] | None = None, *, root: Path = ASSET_ROOT):
        self.root = root.resolve()
        self._paths = {}
        for asset_id, relative in (paths or {}).items():
            if asset_id_from_url("/api/v1/assets/" + asset_id) != asset_id:
                raise ValueError("asset IDs must be canonical unescaped paths")
            if Path(relative).is_absolute() or PureWindowsPath(relative).drive:
                raise ValueError(f"asset registration requires a relative path: {asset_id}")
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root) or path.suffix.lower() not in MEDIA_TYPES:
                raise ValueError(f"asset registration escapes its root or has unsupported type: {asset_id}")
            self._paths[asset_id] = path

    @classmethod
    def from_file(cls, path: Path = ASSET_ROOT / "registry.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("asset registry must map asset IDs to repository-relative paths")
        return cls(data, root=path.parent)

    def available(self, url: str) -> bool:
        asset_id = asset_id_from_url(url)
        return asset_id in self._paths and self._paths[asset_id].is_file()

    def read(self, asset_id: str) -> Asset:
        if asset_id not in self._paths:
            raise KeyError(asset_id)
        path = self._paths[asset_id]
        # Resolve again so a changed symlink cannot escape the trusted registration root.
        if not path.resolve().is_relative_to(self.root):
            raise KeyError(asset_id)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise KeyError(asset_id) from exc
        return Asset(data, MEDIA_TYPES[path.suffix.lower()], '"sha256-' + sha256(data).hexdigest() + '"')
