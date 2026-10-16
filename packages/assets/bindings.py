"""Entity-key bindings replace the primary group and retain native fallback shapes."""

from pathlib import Path

import yaml

from packages.assets.registry import ASSET_ROOT, AssetRegistry, asset_id_from_url
from packages.protocol.math import normalize_entity_key
from packages.protocol.schema import Glb


def load_bindings(path: Path) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("visual bindings must map entityKey to GLB binding")
    result = {}
    for key, binding in data.items():
        key = normalize_entity_key(key)
        if key in result:
            raise ValueError(f"duplicate normalized binding entityKey: {key}")
        visual = Glb.model_validate({"kind": "glb", **binding})
        asset_id_from_url(visual.url)
        result[key] = visual.model_dump(mode="json")
    return result


def apply_bindings(nodes: list[dict], scene_id: str, warnings: list[dict], *, path=None, registry=None):
    bindings = load_bindings(path or ASSET_ROOT / "bindings" / f"{scene_id}.yaml")
    registry = registry or AssetRegistry.from_file()
    remaining = set(bindings)
    for node in nodes:
        if node["entityKey"] not in bindings:
            if node["kind"] != "group" and not node["visuals"]:
                warnings.append(
                    {
                        "code": "asset_fallback",
                        "entityKey": node["entityKey"],
                        "message": "No Newton visual shapes; use colliders or a placeholder",
                    }
                )
            continue
        binding = bindings[node["entityKey"]]
        remaining.remove(node["entityKey"])
        node["fallbackVisuals"] = node["visuals"]
        node["visuals"] = [binding]
        if not registry.available(binding["url"]):
            warnings.append(
                {
                    "code": "asset_missing",
                    "entityKey": node["entityKey"],
                    "message": "Bound asset unavailable; retain native, collider and placeholder fallbacks",
                }
            )
            warnings.append(
                {
                    "code": "asset_fallback",
                    "entityKey": node["entityKey"],
                    "message": "Missing binding falls back to the complete native visual group",
                }
            )
    for key in sorted(remaining):
        warnings.append(
            {
                "code": "asset_missing",
                "entityKey": key,
                "message": "Binding target is absent from the imported scene",
            }
        )
