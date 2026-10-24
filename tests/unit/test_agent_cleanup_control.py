"""The first cancellation cannot interrupt ownership cleanup before its handle proof."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from apps.node.process import CleanupIncomplete
from packages.agents.adapter import SDKAdapter
from packages.agents.runtime import AgentWorker, complete_cleanup
from packages.agents.transport import CredentialRelay
from packages.vault import VaultError


async def test_first_cancel_during_client_close_preserves_owned_process_cleanup_and_original_cancel(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    closed = []

    class Client:
        async def aclose(self):
            entered.set()
            await release.wait()
            closed.append("client")

    relay = CredentialRelay(tmp_path / "relay", None, "unused", "not-executed", deadline=1)
    relay.client = Client()
    relay.process = SimpleNamespace(close=lambda: closed.append("process"))
    closing = asyncio.create_task(relay.close())
    await asyncio.wait_for(entered.wait(), 2)
    closing.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert closed == ["process", "client"]


async def test_cleanup_failure_after_first_caller_cancel_keeps_cancel_identity_and_cause():
    entered, release = asyncio.Event(), asyncio.Event()

    async def cleanup():
        entered.set()
        await release.wait()
        raise CleanupIncomplete("controlled cleanup remains unconfirmed")

    task = asyncio.create_task(complete_cleanup(cleanup()))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task
    assert isinstance(cancelled.value.__cause__, CleanupIncomplete)


async def test_known_vault_failure_before_spawn_is_clean_but_unknown_attempt_is_not(tmp_path, monkeypatch):
    calls = []

    class Vault:
        def adapter_environment(self, bindings):
            raise VaultError("controlled pre-spawn credential resolution failure")

    def constructor(*args, **kwargs):
        calls.append(True)
        raise AssertionError("No process may be constructed before credential resolution")

    monkeypatch.setattr("packages.agents.transport.OwnedProcess", constructor)
    adapter = SDKAdapter(tmp_path)
    adapter.vault = Vault()
    record = {"jobId": "job-before-spawn", "model": "anthropic/not-executed",
              "plan": {"credentialRef": "llm/not-read"}}
    with pytest.raises(VaultError):
        async with adapter.open(record, time.monotonic() + 5, tmp_path / "run"):
            raise AssertionError("The adapter must fail before yielding")
    assert calls == [] and adapter.release_confirmed(record["jobId"])
    worker = AgentWorker(tmp_path / "never-started", {}, {}, deadline=time.monotonic() + 5)
    adapter._workers[record["jobId"]] = [worker]
    assert adapter.release_confirmed(record["jobId"])
    worker.spawn_attempted = True
    adapter._workers[record["jobId"]] = [worker]
    assert not adapter.release_confirmed(record["jobId"])
