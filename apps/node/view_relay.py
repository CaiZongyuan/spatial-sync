"""Node-owned authenticated relay; independent of heartbeat and trainer stepping."""

import asyncio
import contextlib
import json
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect


def latest(owned):
    # Existing/recovered work roots never grant this in-memory ownership capability.
    if not owned.view_active.is_set() or not owned.thread.is_alive():
        return None
    path = owned.view_directory / "latest.json"
    try:
        if path.stat().st_size > 1024 * 1024:
            return None
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


async def relay(owned):
    parsed = urlsplit(owned.node.url)
    path = f"/api/v1/jobs/{owned.assignment['jobId']}/view-frames"
    url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, path, "", ""))
    while owned.thread.is_alive():
        value = await asyncio.to_thread(latest, owned)
        if value is None:
            await asyncio.sleep(0.05)
            continue
        try:
            async with connect(
                url, additional_headers=owned.headers(), open_timeout=2, close_timeout=1,
                max_size=256 * 1024, proxy=None,
            ) as socket:
                sent_hello, sent_error, sent = None, None, {}
                while owned.thread.is_alive() and owned.view_active.is_set():
                    value = await asyncio.to_thread(latest, owned)
                    if value is None and (sent_hello or sent_error):
                        break  # Callback has closed its channel; cached poses cease to be live.
                    if value:
                        error = value.get("error")
                        hello = value.get("hello")
                        if error and error != sent_error:
                            await asyncio.wait_for(socket.send(json.dumps(error)), 2)
                            sent_error = error
                        if hello:
                            if sent_hello != hello:
                                await asyncio.wait_for(socket.send(json.dumps(hello)), 2)
                                sent_hello, sent = hello, {}
                            for frame in value.get("frames", ()):
                                index, seq = frame["envIndex"], frame["payload"]["seq"]
                                if seq > sent.get(index, -1):
                                    # Every reconnect replays an actual complete latest snapshot.
                                    await asyncio.wait_for(socket.send(json.dumps(frame, allow_nan=False)), 2)
                                    sent[index] = seq
                    # Read producer errors/terminal closure without another receive task/queue.
                    try:
                        reply = await asyncio.wait_for(socket.recv(), 0.05)
                        if json.loads(reply).get("type") in ("error", "ended"):
                            break
                    except TimeoutError:
                        pass
        except (OSError, TimeoutError, ValueError):
            pass  # A view connection failure never cancels training or delays heartbeat.
        except Exception:
            pass  # Includes protocol/handshake closure; retry only while this live owner exists.
        await asyncio.sleep(0.2)


async def relay_views(node):
    tasks = {}
    try:
        while True:
            for identity, owned in tuple(node.active.items()):
                if identity not in tasks:
                    tasks[identity] = asyncio.create_task(relay(owned), name="node-job-view-relay")
            for identity, task in tuple(tasks.items()):
                if task.done():
                    with contextlib.suppress(asyncio.CancelledError):
                        task.result()
                    del tasks[identity]
            await asyncio.sleep(0.05)
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
