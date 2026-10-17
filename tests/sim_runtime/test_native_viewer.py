import asyncio
import multiprocessing as mp
import os
import signal
import socket
from urllib.parse import urlparse

import httpx
import pytest

pytest.importorskip("newton")
pytest.importorskip("viser")
pytestmark = pytest.mark.newton

from apps.api.app.sources.newton_source import NewtonSource
from packages.protocol.schema import Command, SessionOptions


def listening(url):
    with socket.socket() as connection:
        return connection.connect_ex(("127.0.0.1", urlparse(url).port)) == 0


async def test_source_viewer_reuses_runner_and_cleans_servers_after_replacement_and_crash():
    sources, urls, pids = [], [], []
    try:
        for attempt in range(2):
            source = NewtonSource("viewer-lifecycle")
            sources.append(source)
            await source.start("falling-boxes", SessionOptions(device="cpu"))
            pid = source.stats()["runnerPID"]
            pids.append(pid)
            assert pid != os.getpid() and source.stats()["device"] == "cpu"
            await source.execute(Command(requestId=f"pause-{attempt}", command="pause"))
            reset = await source.execute(Command(requestId=f"reset-{attempt}", command="reset"))
            first, second = await asyncio.gather(source.open_viewer(), source.open_viewer())
            assert first == second and first["status"] == "ready" and first["backendPid"] == pid
            urls.append(first["url"])
            assert urlparse(first["url"]).hostname == "127.0.0.1"
            async with httpx.AsyncClient(trust_env=False) as client:
                assert (await client.get(first["url"])).status_code == 200
            assert source.stats()["viewer"]["clientCount"] == 0
            assert source.latest_frame().simTime == reset.frame.simTime == 0
            step = await source.execute(
                Command(requestId=f"step-{attempt}", command="step", args={"count": 24})
            )
            assert step.frame.simTime == pytest.approx(0.1)
            assert (await source.open_viewer()) == first
            if attempt == 0:
                await source.close()
                assert not listening(first["url"])
            else:
                assert pid != pids[0] and not listening(urls[0])
                os.kill(pid, signal.SIGTERM)
                async with asyncio.timeout(5):
                    while source.stats()["runnerAlive"]:
                        await asyncio.sleep(0.01)
                assert source.stats()["viewer"]["status"] == "unavailable"
                assert source.stats()["viewer"]["url"] is None
                assert not listening(first["url"])
                with pytest.raises(RuntimeError, match="unavailable"):
                    await source.open_viewer()
    finally:
        for source in sources:
            await source.close()
    assert not any(source.stats()["readerThreadsAlive"] for source in sources)
    assert not set(pids).intersection(child.pid for child in mp.active_children())
    assert all(not listening(url) for url in urls)
