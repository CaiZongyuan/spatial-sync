"""Invocation table public history and terminal-wait contract."""

import asyncio

import pytest

from packages.info.invocations import HistoryUnavailable, InvocationTable
from packages.protocol.schema import Invocation


async def test_terminal_wait_returns_exact_result_even_when_history_eviction_precedes_resumption():
    archive = {}

    async def lookup(invocation_id):
        return archive.get(invocation_id)

    table = InvocationTable(evicted_lookup=lookup)
    active = Invocation(
        invocationId="target",
        requestId="target-request",
        entityKey="/Device",
        action="start",
        status="accepted",
        epoch=0,
        createdSimTime=0,
        createdAtMs=1,
    )
    table.apply(active)
    waiter = asyncio.create_task(table.wait("target", 1))
    await asyncio.sleep(0)
    completed = active.model_copy(update={"status": "succeeded", "result": {"done": True}, "endedSimTime": 2})
    archive[completed.invocationId] = completed
    table.apply(completed)
    for index in range(1001):
        table.apply(
            active.model_copy(
                update={"invocationId": f"other-{index}", "requestId": f"other-{index}", "status": "rejected"}
            )
        )
    value, timed_out = await waiter
    assert timed_out is False
    assert value == completed


@pytest.mark.parametrize("provider", ["absent", "missing", "failed"])
async def test_evicted_known_requests_never_become_new_work_when_history_is_unavailable(provider):
    async def lookup(invocation_id):
        if provider == "failed":
            raise OSError("recording is unavailable")
        return None

    table = InvocationTable(evicted_lookup=None if provider == "absent" else lookup)
    for index in range(1002):
        table.apply(
            Invocation(
                invocationId=f"inv-{index}",
                requestId=f"request-{index}",
                entityKey="/Device",
                action="start",
                status="rejected",
                epoch=0,
                createdSimTime=0,
                createdAtMs=index,
            )
        )
    assert len(table.terminal) == 1000 and len(table.requests) == 1002
    for call in (table.lookup("inv-0"), table.lookup_request("request-0"), table.wait("inv-0", 0)):
        with pytest.raises(HistoryUnavailable) as caught:
            await call
        assert caught.value.invocation_id == "inv-0"
    assert await table.lookup("unknown") is None
    assert await table.lookup_request("unknown") is None
    assert (await table.lookup("inv-1001")).requestId == "request-1001"
