"""The first cancellation cannot interrupt ownership cleanup before its handle proof."""

import asyncio
from types import SimpleNamespace

import pytest

from packages.agents.transport import CredentialRelay


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
