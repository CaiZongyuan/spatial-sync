"""Invocation table public history and terminal-wait contract."""

import asyncio

from packages.info.invocations import InvocationTable
from packages.protocol.schema import Invocation


async def test_terminal_wait_returns_exact_result_even_when_history_eviction_precedes_resumption():
    table = InvocationTable()
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
