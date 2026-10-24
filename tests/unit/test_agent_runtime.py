"""Real child environments and owned handles, without model credentials or requests."""

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from packages.agents.runtime import AgentWorker, clean_environment
from packages.info.plans import PlanError


def environment(root):
    return clean_environment(
        root,
        gateway_url="http://127.0.0.1:8048",
        session_id="session-owned",
        run_id="job-agent",
        tool_token="lw-agent-generated",
        provider_url="http://127.0.0.1:8049",
        provider_token="local-provider-grant",
    )


def test_real_child_receives_only_owned_home_and_loopback_grants_not_parent_auth_or_user_configuration(
    tmp_path, monkeypatch
):
    for name in (
        "AWS_ACCESS_KEY_ID",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "HTTP_PROXY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_EXTRA_BODY",
        "PYTHONPATH",
    ):
        monkeypatch.setenv(name, "PRIVATE_PARENT_SENTINEL")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "PRIVATE_REAL_PROVIDER_KEY")
    monkeypatch.setenv("LW_TOKEN", "PRIVATE_PARENT_OPERATOR")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://must-never-reach.example")
    receipt = subprocess.run(
        [sys.executable, "-I", "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        env=environment(tmp_path),
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert "PRIVATE_" not in receipt.stdout
    child = json.loads(receipt.stdout)
    assert child["ANTHROPIC_API_KEY"] == "local-provider-grant"
    assert child["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8049"
    assert child["LW_TOKEN"] == "lw-agent-generated"
    for name in ("HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR", "TEMP", "APPDATA"):
        assert str(tmp_path) in child[name]


async def test_expired_or_revoked_overall_deadline_cannot_start_another_case_worker(tmp_path):
    for deadline, active in ((time.monotonic() - 1, lambda: True), (time.monotonic() + 10, lambda: False)):
        root = tmp_path / "not-started"
        worker = AgentWorker(root, {}, {}, deadline=deadline, active=active)
        with pytest.raises(PlanError, match="deadline or authority"):
            await worker.run()
        assert worker.process is None and not root.exists()


async def test_worker_cancellation_waits_for_the_creating_handle_cleanup(monkeypatch, tmp_path):
    import packages.agents.runtime as runtime

    cleanup = []

    class Child:
        returncode = None

        def poll(self):
            return None

    class Owned:
        def __init__(self, *args, **kwargs):
            self.process = Child()

        def close(self):
            time.sleep(0.03)
            cleanup.append("confirmed")

    monkeypatch.setattr(runtime, "OwnedProcess", Owned)
    worker = AgentWorker(tmp_path / "case", {}, {}, deadline=time.monotonic() + 10)
    task = asyncio.create_task(worker.run())
    while worker.process is None:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanup == ["confirmed"]


async def test_whole_worker_deadline_reaps_real_child_and_grandchild_through_owned_handle(
    monkeypatch, tmp_path
):
    import packages.agents.runtime as runtime
    from apps.node.process import OwnedProcess

    code = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)']); "
        "print(child.pid, flush=True); time.sleep(60)"
    )

    def launch_fixture(command, **kwargs):
        return OwnedProcess([sys.executable, "-I", "-c", code], **kwargs)

    monkeypatch.setattr(runtime, "OwnedProcess", launch_fixture)
    worker = AgentWorker(
        tmp_path / "real-case", {}, environment(tmp_path / "environment"), deadline=time.monotonic() + 1
    )
    with pytest.raises(PlanError, match="deadline or authority"):
        await worker.run()
    assert worker.process.process.poll() is not None
    assert worker.process.cleanup.read_text(encoding="ascii") == "clean"
    assert (tmp_path / "real-case/trace.jsonl").read_text().strip().isdigit()
