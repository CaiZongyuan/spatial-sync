"""Run the pinned Blender and MCP probes and record the SF0 support baseline."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from pxr import Usd

from factory.support import BLENDER_DOWNLOAD, MCP_COMMIT

REPO_ROOT = Path(__file__).resolve().parents[1]


def _python(mcp_executable):
    parent = Path(mcp_executable).parent
    for name in ("python.exe", "python"):
        candidate = parent / name
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def _addon_path(python):
    code = (
        "import blender_mcp, pathlib; "
        "print((pathlib.Path(blender_mcp.__file__).parent / 'bundled/addon.py').resolve())"
    )
    result = subprocess.run([str(python), "-c", code], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "cannot locate the bundled addon")
    return Path(result.stdout.strip())


def _install_addon(blender_executable, addon):
    root = Path(blender_executable).resolve().parent
    versions = sorted(path for path in root.glob("[0-9]*.[0-9]*") if path.is_dir())
    if not versions:
        raise RuntimeError("cannot find the Blender version directory")
    destination = versions[-1] / "scripts/addons/blender_mcp_addon.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(addon, destination)
    return destination


def _run(command, *, timeout):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return {
        "command": [str(item) for item in command],
        "returncode": result.returncode,
        "stdout": (result.stdout or "")[-8000:],
        "stderr": (result.stderr or "")[-8000:],
    }


def verify(blender, mcp, output):
    blender, mcp, output = Path(blender), Path(mcp), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.parent / "blender"
    work.mkdir(parents=True, exist_ok=True)
    python = _python(mcp)
    result = {
        "version": None,
        "mcpForBlender": None,
        "mcpCommit": MCP_COMMIT,
        "blenderDownload": BLENDER_DOWNLOAD,
        "addon": None,
        "configuration": {
            "safeMode": "BLENDER_MCP_SAFE_MODE=1",
            "telemetry": "DISABLE_TELEMETRY=true",
            "autoStartServer": False,
            "providersEnabled": False,
        },
        "verified": {
            "backgroundScript": False,
            "safeMode": False,
            "telemetryDisabled": False,
            "providersDisabled": False,
        },
        "evidence": {},
        "notes": [
            "The server package is 2.2.2; the addon bl_info version is 1.8 in the same pinned commit.",
            "The addon defaults to auto-start; the factory baseline explicitly disables it.",
            "The installed 2.2.2 wheel omits blender_mcp.config, so telemetry cannot initialize; "
            "the disable flag is still checked with a stub config.",
            "The addon socket is not auto-started; Blender background mode refuses the interactive server.",
            "BLENDER_MCP_SAFE_MODE covers the MCP path only; "
            "the addon socket accepts local code from any process.",
        ],
    }
    try:
        addon = _addon_path(python)
        installed = _install_addon(blender, addon)
        blender_out, usd = work / "probe.json", work / "probe.usda"
        blender_run = _run(
            [
                blender,
                "--background",
                "--factory-startup",
                "--python",
                REPO_ROOT / "factory/blender_probe.py",
                "--",
                "--out",
                blender_out,
                "--usd",
                usd,
            ],
            timeout=180,
        )
        mcp_out = work / "mcp.json"
        mcp_run = _run(
            [python, REPO_ROOT / "factory/mcp_probe.py", "--server", mcp, "--out", mcp_out],
            timeout=180,
        )
        result["evidence"] = {
            "addon": str(installed),
            "blender": blender_run,
            "mcp": mcp_run,
        }
        if blender_out.is_file():
            blender_result = json.loads(blender_out.read_text(encoding="utf-8"))
            result["version"] = blender_result.get("blenderVersion")
            result["addon"] = blender_result.get("addon")
            result["configuration"]["autoStartServerDefault"] = blender_result.get(
                "autoStartServerDefault"
            )
            result["evidence"]["blenderResult"] = blender_result
            exported = Path((blender_result.get("usd") or {}).get("path") or "")
            if not exported.is_absolute():
                exported = REPO_ROOT / exported
            stage = Usd.Stage.Open(str(exported)) if exported.is_file() else None
            result["evidence"]["usdDefaultPrim"] = (
                str(stage.GetDefaultPrim().GetPath()) if stage is not None else None
            )
            result["verified"]["backgroundScript"] = bool(
                blender_result.get("background")
                and (blender_result.get("usd") or {}).get("bytes")
                and stage is not None
            )
            providers = blender_result.get("providersConfigured") or {}
            result["verified"]["providersDisabled"] = (
                blender_result.get("autoStartServerConfigured") is False
                and all(value is False for value in providers.values())
                and bool(providers)
            )
        if mcp_out.is_file():
            mcp_result = json.loads(mcp_out.read_text(encoding="utf-8"))
            result["mcpForBlender"] = mcp_result.get("serverVersion")
            result["configuration"]["telemetryRuntime"] = (
                "unavailable (wheel omits blender_mcp.config)"
                if mcp_result.get("telemetryPackagingGap")
                else "checked"
            )
            result["evidence"]["mcpResult"] = mcp_result
            result["verified"]["safeMode"] = bool(
                mcp_result.get("safeModeRejected") and mcp_result.get("allowedReachedConnection")
            )
            result["verified"]["telemetryDisabled"] = bool(
                mcp_result.get("telemetryDisabled")
                and (result["evidence"].get("blenderResult") or {}).get("contentTelemetryConsent") is False
            )
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        result["evidence"]["error"] = f"{type(exc).__name__}: {exc}"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
