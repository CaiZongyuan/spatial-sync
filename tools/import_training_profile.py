"""Import measurement evidence; matching approved queued work may then dispatch."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/cli"))

from lw.client import GatewayClient

from packages.library.transactions import durable_json
from packages.training.profiles import measured_profile


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--resource", required=True, help="Advertised GPU UUID, or cpu")
    parser.add_argument("--measurement", required=True, type=Path)
    parser.add_argument("--capabilities", required=True, type=Path)
    parser.add_argument("--url")
    args = parser.parse_args()
    for path in (args.measurement.resolve(), args.capabilities.resolve()):
        if any((ancestor / "task.yaml").is_file() for ancestor in path.parents):
            raise ValueError("Measurements and node profiles must live outside the Task package")
    async with GatewayClient(args.url) as client:
        job = await client.job_show(args.job)
        plan = await client.plan_show(job["planId"], revision=job["planRevision"])
        nodes = [value for value in await client.node_list() if value["id"] == "local"]
        if not nodes:
            raise ValueError("Configure and start the local node before importing a measurement")
        node = nodes[0]
    raw = args.measurement.read_bytes()
    profile, digest = measured_profile(raw, job, plan, node, args.resource)
    capabilities = json.loads(args.capabilities.read_text(encoding="utf-8"))
    if capabilities["hardware"] != node["hardware"]:
        raise ValueError("Capability file belongs to another node hardware identity")
    existing = {value["key"]: value for value in capabilities.get("profiles", [])}
    existing[profile["key"]] = profile
    capabilities["profiles"] = list(existing.values())
    evidence = args.capabilities.parent / "measurements" / (digest + ".json")
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(raw)
    durable_json(args.capabilities, capabilities)
    print(json.dumps({"profile": profile, "measurement": str(evidence), "queuedJobsMayDispatch": True}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, OSError, StopIteration) as error:
        raise SystemExit(str(error)) from None
