"""Select CI nodes only after full collection; retain each worker's evidence."""

import hashlib
import json
import os
from pathlib import Path

import pytest

PREFIX = "SPATIAL_CI_PYTEST_"


def shard():
    index, count = (os.environ.get(PREFIX + key) for key in ("INDEX", "COUNT"))
    if index is None and count is None:
        return 1, 1
    if (
        not index
        or not count
        or not index.isascii()
        or not count.isascii()
        or not (index.isdecimal() and count.isdecimal() and 1 <= int(index) <= int(count))
    ):
        raise pytest.UsageError("CI shard needs both INDEX and COUNT with 1 <= INDEX <= COUNT")
    return int(index), int(count)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    index, count = shard()
    collected = [item.nodeid for item in items]
    selected, deselected = [], []
    for item in items:
        bucket = int.from_bytes(hashlib.sha256(item.nodeid.encode("utf-8")).digest(), "big") % count
        (selected if bucket == index - 1 else deselected).append(item)
    if deselected:
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)
    directory = os.environ.get(PREFIX + "MANIFEST_DIR")
    if directory:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
        workers = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))
        manifest = {
            "schemaVersion": 1,
            "lane": os.environ.get(PREFIX + "LANE", "local"),
            "shardIndex": index,
            "shardCount": count,
            "worker": worker,
            "workerCount": workers,
            "expectedWorkers": int(os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS", str(workers))),
            "collectOnly": config.getoption("collectonly"),
            "collected": collected,
            "selected": [item.nodeid for item in selected],
            "deselected": [item.nodeid for item in deselected],
            "exitStatus": None,
        }
        config._ci_collection_manifest = (Path(directory) / f"collection-{worker}.json", manifest)
        _write(config)


def _write(config):
    path, manifest = config._ci_collection_manifest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if hasattr(config, "_ci_collection_manifest"):
        config._ci_collection_manifest[1]["exitStatus"] = int(exitstatus)
        _write(config)


def pytest_terminal_summary(terminalreporter):
    directory = os.environ.get(PREFIX + "MANIFEST_DIR")
    if not directory:
        return
    paths = sorted(Path(directory).glob("collection-*.json"))
    if not paths:
        return
    records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    first = records[0]
    terminalreporter.write_line(
        json.dumps(
            {
                "ciShard": [first["lane"], first["shardIndex"], first["shardCount"]],
                "workers": [record["worker"] for record in records],
                "collected": len(first["collected"]),
                "selected": len(first["selected"]),
                "deselected": len(first["deselected"]),
                "manifests": [str(path) for path in paths],
                "selectedNodeIds": first["selected"] if first["shardCount"] > 1 else "all collected nodes",
                "deselectedNodeIds": first["deselected"],
            },
            ensure_ascii=False,
        )
    )
