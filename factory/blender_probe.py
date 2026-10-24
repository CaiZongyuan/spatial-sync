"""Blender-side probe: registered addon defaults, background USD export, no auto-server."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy

PROVIDERS = ("polyhaven", "hyper3d", "hunyuan3d", "sketchfab", "tripo", "meshy", "polypizza")


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--usd", required=True)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def main():
    options = arguments()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addons = Path(bpy.utils.script_paths()[0]) / "addons"
    if str(addons) not in sys.path:
        sys.path.insert(0, str(addons))
    bpy.ops.preferences.addon_enable(module="blender_mcp_addon")
    import blender_mcp_addon as addon

    scene = bpy.context.scene
    preferences = bpy.context.preferences.addons.get("blender_mcp_addon")
    consent = bool(preferences.preferences.telemetry_consent) if preferences else None
    providers = {name: bool(getattr(scene, f"blendermcp_use_{name}")) for name in PROVIDERS}
    auto_start_default = bool(scene.blendermcp_auto_start_server)
    scene.blendermcp_auto_start_server = False
    for name in PROVIDERS:
        setattr(scene, f"blendermcp_use_{name}", False)
    configured = {name: bool(getattr(scene, f"blendermcp_use_{name}")) for name in PROVIDERS}
    server = addon.BlenderMCPServer()
    server.start()
    background_server_running = bool(server.running)
    usd = Path(options.usd)
    usd.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.mesh.primitive_cube_add(size=0.2)
    bpy.ops.wm.usd_export(filepath=str(usd), export_materials=False)
    result = {
        "blenderVersion": bpy.app.version_string.split()[0],
        "background": bool(bpy.app.background),
        "addon": {"name": addon.bl_info["name"], "version": ".".join(map(str, addon.bl_info["version"]))},
        "contentTelemetryConsent": consent,
        "providersDefault": providers,
        "providersConfigured": configured,
        "autoStartServerDefault": auto_start_default,
        "autoStartServerConfigured": bool(scene.blendermcp_auto_start_server),
        "interactiveServerRefusedInBackground": not background_server_running,
        "usd": {
            "path": str(usd),
            "bytes": usd.stat().st_size if usd.is_file() else 0,
            "sha256": hashlib.sha256(usd.read_bytes()).hexdigest() if usd.is_file() else None,
        },
    }
    Path(options.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not (
        result["background"]
        and result["addon"]["version"]
        and result["autoStartServerConfigured"] is False
        and all(value is False for value in configured.values())
        and consent is False
        and result["usd"]["bytes"] > 0
    ):
        raise SystemExit("Blender probe configuration check failed")


if __name__ == "__main__":
    main()
