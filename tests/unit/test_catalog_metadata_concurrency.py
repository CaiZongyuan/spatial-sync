"""Independent hosts can initialize and use the same fresh local catalog."""

import multiprocessing
import traceback

from packages.catalog_metadata import MetadataStore


def _initialize_shared_store(root, ready, start, results, index):
    ready.put(index)
    start.wait(timeout=20)
    try:
        store = MetadataStore(root)
        profile = {"id": f"worker-{index}", "dataset": f"fixture/worker-{index}", "recipe": "simready"}
        store.register_collection(profile)
        assert store.collection(profile["id"])["dataset"] == profile["dataset"]
        results.put(None)
    except BaseException:
        results.put(traceback.format_exc())


def test_concurrent_fresh_store_initialization_preserves_all_writes(tmp_path):
    context = multiprocessing.get_context("spawn")
    for iteration in range(3):
        root = tmp_path / str(iteration)
        ready, results, start = context.Queue(), context.Queue(), context.Event()
        processes = [
            context.Process(target=_initialize_shared_store, args=(root, ready, start, results, index))
            for index in range(8)
        ]
        try:
            for process in processes:
                process.start()
            for _ in processes:
                ready.get(timeout=20)
            start.set()
            failures = [results.get(timeout=40) for _ in processes]
            for process in processes:
                process.join(timeout=5)
            assert all(process.exitcode == 0 for process in processes)
            assert failures == [None] * len(processes), failures
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
            ready.close()
            results.close()
        store = MetadataStore(root)
        assert {profile["id"] for profile in store.collections()} == {
            "simready",
            "scenario",
            "chemlab",
            *(f"worker-{index}" for index in range(8)),
        }
