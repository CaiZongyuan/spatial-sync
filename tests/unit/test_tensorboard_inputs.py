"""An in-flight cold launch cannot hide a newly running Job's live log input."""

import asyncio
from types import SimpleNamespace

from packages.training.tensorboard import TensorBoardService


async def test_pending_readiness_then_new_job_updates_inputs_without_duplicate_concurrent_owners(tmp_path):
    jobs = {
        "a": {"jobId": "a", "status": "running", "nodeId": "local", "leaseId": "lease-a"},
    }
    manager = SimpleNamespace(
        settings=SimpleNamespace(state_root=tmp_path),
        library_assets=SimpleNamespace(roots={"user": tmp_path / "library"}),
        jobs=SimpleNamespace(records=jobs, node=SimpleNamespace(root=tmp_path / "node")),
    )
    service = TensorBoardService(manager)
    entered, release = asyncio.Event(), asyncio.Event()
    launches = []

    async def launch():
        launches.append(dict(service.input_directories))
        if len(launches) == 1:
            entered.set()
            await release.wait()
        service.process = SimpleNamespace(process=SimpleNamespace(poll=lambda: None))
        service.url = f"http://owned-fixture/{len(launches)}"
        return {"url": service.url}

    service._launch = launch
    first = asyncio.create_task(service.start())
    await entered.wait()
    jobs["b"] = {"jobId": "b", "status": "running", "nodeId": "local", "leaseId": "lease-b"}
    second, third = asyncio.create_task(service.start()), asyncio.create_task(service.start())
    await asyncio.sleep(0)
    assert not first.done() and not second.done() and not third.done()
    release.set()
    results = await asyncio.gather(first, second, third)
    assert results == [{"url": "http://owned-fixture/2"}] * 3
    assert len(launches) == 2
    assert set(launches[0]) == {"published", "live-a"}
    assert set(launches[1]) == {"published", "live-a", "live-b"}
