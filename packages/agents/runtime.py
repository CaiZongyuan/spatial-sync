"""Own one restricted SDK worker and its descendants under the Run's deadline."""

import asyncio
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from apps.node.process import OwnedProcess
from packages.info.plans import PlanError


async def complete_cleanup(awaitable):
    task = asyncio.create_task(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        try:
            task.result()
        except BaseException as error:
            raise asyncio.CancelledError from error
        raise asyncio.CancelledError
    return task.result()


def runtime_identity():
    try:
        distribution = importlib.metadata.distribution("claude-agent-sdk")
        if distribution.version != "0.2.174":
            raise ValueError
        executable = distribution.locate_file(
            "claude_agent_sdk/_bundled/" + ("claude.exe" if os.name == "nt" else "claude")
        )
        with executable.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        raise PlanError(
            "agent_runtime_unavailable", "Install the pinned optional Agent runtime", status=422
        ) from None
    root = Path(__file__).resolve().parents[2]
    sources = [
        *(root / "packages/agents").glob("*.py"),
        *(root / "apps/cli/lw").glob("*.py"),
        root / "apps/node/process.py",
        root / "apps/node/job_launcher.py",
    ]
    implementation = hashlib.sha256()
    for source in sorted(sources):
        implementation.update(source.relative_to(root).as_posix().encode() + b"\0" + source.read_bytes())
    return {
        "adapter": "claude-agent-sdk",
        "sdkVersion": distribution.version,
        "bundledCliSha256": digest,
        "pythonVersion": sys.version.split()[0],
        "adapterSha256": implementation.hexdigest(),
    }


def sdk_configuration(harness, *, model, thinking, statement, requests, max_output_tokens):
    if thinking not in {"default", "none", "low", "medium", "high", "xhigh", "max"}:
        raise PlanError("agent_thinking_unavailable", "The selected thinking mode is unavailable", status=422)
    documents = dict(harness.files)
    instructions = [
        "Use only the granted lab MCP tools. Human gates are denied during evaluation.",
        documents["harness/AGENTS.md"].decode("utf-8"),
    ]
    instructions.extend(data.decode("utf-8") for name, data in harness.files if name.startswith("skills/"))
    return {
        "platformMcp": harness.platform_mcp,
        "systemPrompt": "\n\n".join(instructions),
        "model": model,
        "thinking": thinking,
        "statement": statement,
        "maxTurns": requests * 2 + 1,
        "maxOutputTokens": max_output_tokens,
    }


def clean_process_environment(root):
    """Only owned homes and required OS paths are inherited by managed processes."""
    root = Path(root).resolve()
    home, temporary = root / "home", root / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    environment = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR") if name in os.environ}
    system_path = (
        [str(Path(environment["SYSTEMROOT"]) / "System32")] if os.name == "nt" else ["/usr/bin", "/bin"]
    )
    environment.update(
        {
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), *system_path]),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(home / "AppData/Roaming"),
            "LOCALAPPDATA": str(home / "AppData/Local"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "TMPDIR": str(temporary),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return environment


def clean_environment(root, *, gateway_url, session_id, run_id, tool_token, provider_url, provider_token):
    """Build from an allowlist before the SDK can merge its parent's environment."""
    for url in (gateway_url, provider_url):
        endpoint = urlsplit(url)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "::1"}
            or endpoint.username
            or endpoint.password
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise PlanError(
                "agent_transport_unavailable", "Managed transports require owned loopback URLs", status=422
            )
    environment = clean_process_environment(root)
    environment.update(
        {
            "LW_URL": gateway_url,
            "LW_SESSION": session_id,
            "LW_AGENT_RUN": run_id,
            "LW_TOKEN": tool_token,
            "ANTHROPIC_BASE_URL": provider_url,
            "ANTHROPIC_API_KEY": provider_token,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
        }
    )
    return environment


class AgentWorker:
    def __init__(
        self,
        root,
        configuration,
        environment,
        *,
        files=(),
        deadline,
        active=lambda: True,
        on_cleanup=lambda: None,
    ):
        self.root, self.configuration, self.environment = Path(root).resolve(), configuration, environment
        self.files = files
        self.deadline, self.active = deadline, active
        self.on_cleanup = on_cleanup
        self.process = None

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or not self.active():
            raise PlanError("agent_run_inactive", "The Agent Run deadline or authority expired", status=403)
        return remaining

    async def run(self):
        self.remaining()
        self.root.mkdir(parents=True, exist_ok=False)
        config = self.root / "request.json"
        config.write_text(json.dumps(self.configuration, ensure_ascii=False), encoding="utf-8")
        config.chmod(0o600)
        # Only public inputs and the exact frozen Harness are written by the caller.
        public = self.root / "public"
        public.mkdir()
        for name, data in self.files:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
                raise ValueError("Invalid frozen Harness path")
            file = public / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
        trace = self.root / "trace.jsonl"
        started = time.monotonic()
        try:
            with trace.open("xb") as output:
                import subprocess

                self.remaining()
                self.process = OwnedProcess(
                    [sys.executable, "-I", str(Path(__file__).with_name("sdk_worker.py")), str(config)],
                    cwd=public,
                    env=self.environment,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                )
                while self.process.process.poll() is None:
                    await asyncio.sleep(min(0.05, self.remaining()))
                return {
                    "code": self.process.process.returncode,
                    "trace": trace,
                    "durationSeconds": time.monotonic() - started,
                }
        finally:
            self.on_cleanup()
            if self.process is not None:
                await complete_cleanup(asyncio.to_thread(self.process.close))
