"""Import, validate and launch an exact factory output through the public HTTP API.

Run against an owned empty gateway with isolated library/state/recording roots.
This creates no alternate device control or workflow pipeline.
"""

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from apps.cli.lw.client import GatewayClient
from packages.library.common import content_hash
from tools.cell_passage import _install


async def launch(library, url, output):
    library, output = Path(library).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a fresh public-API receipt directory")
    output.mkdir(parents=True)

    def save(name, value):
        (output / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    async with GatewayClient(url=url) as client:
        health = await client.request("GET", "/api/v1/health")
        save("health-before", health)
        if health["sessionCount"]:
            raise ValueError("The selected gateway already owns a session")
        scene = library / "scenes/cell-passage/1"
        selected = await _install(client, library, output, content_hash(scene))
        report = json.loads((output / "validation.json").read_text(encoding="utf-8"))
        if any(row["status"] == "fail" for row in report.get("checks", [])):
            raise ValueError("Public scene validation reported a blocking failure")
        started = await client.request(
            "POST",
            "/api/v1/sessions",
            timeout=190,
            json={
                "sceneVersion": selected,
                "source": "newton",
                "seed": 42,
                "continueWithoutObservers": True,
                "requestId": uuid.uuid4().hex,
            },
        )
        save("session-launch", started)
        deadline = time.monotonic() + 190
        while "sessionId" not in started and "requestId" in started and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
            pending = await client.session_request_show(started["requestId"])
            save("session-request", pending)
            if pending.get("error"):
                raise ValueError("Public launch failed: " + str(pending["error"]))
            started = pending.get("result") or pending
        identifier = started["sessionId"]
        route = "/api/v1/sessions/" + identifier
        save("manifest", await client.request("GET", route + "/scene"))
        save("snapshot", await client.request("GET", route + "/snapshot"))
        save("state", await client.request("GET", route + "/state"))
        print(
            json.dumps(
                {
                    "sceneVersion": selected,
                    "sessionId": identifier,
                    "gateway": url,
                    "deviceActionsQualified": False,
                },
                indent=2,
            )
        )
        return started


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(launch(args.library, args.url, args.out))
