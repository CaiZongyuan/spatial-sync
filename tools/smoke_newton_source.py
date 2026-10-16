"""Bounded spawn/IPC proof without a gateway server or a GPU import in the caller."""

import argparse
import asyncio
import json
import os
import signal
import sys
from importlib.metadata import version
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.app.sources.newton_source import NewtonSource
from packages.protocol.schema import Command, SessionOptions, SessionState


async def smoke(args):
    source = NewtonSource("cli-smoke")
    try:
        manifest = await source.start(args.scene, SessionOptions(device=args.device))
        initial = source.latest_frame()
        await asyncio.sleep(1.1)
        pause = await source.execute(Command(requestId="pause", command="pause"))
        step = await source.execute(Command(requestId="step", command="step", args={"count": 24}))
        reset = await source.execute(Command(requestId="reset", command="reset"))
        assert pause.frame.seq < step.frame.seq < reset.frame.seq
        assert reset.frame.epoch == 1 and reset.frame.simTime == 0
        for before, after in zip(initial.poses, reset.frame.poses, strict=True):
            assert before[0] == after[0]
            assert all(abs(a - b) < 1e-6 for a, b in zip(before[1:4], after[1:4]))
            assert abs(sum(a * b for a, b in zip(before[4:], after[4:]))) > 0.99999
        assert pause.frame.seq == pause.state.seq
        assert pause.frame.simTime == pause.ack.simTime
        metrics = source.stats()
        result = {
            "status": "passed",
            "device": args.device,
            "scene": args.scene,
            "versions": {name: version(name) for name in ("newton", "warp-lang", "pydantic")},
            "sceneRevision": manifest.sceneRevision,
            "streamBodyCount": len(initial.poses),
            "initial": initial.model_dump(mode="json"),
            "pause": pause.model_dump(mode="json"),
            "step": step.model_dump(mode="json"),
            "reset": reset.model_dump(mode="json"),
            "metrics": metrics,
            "physicsImportedInCaller": "newton" in sys.modules or "warp" in sys.modules,
        }
        assert not result["physicsImportedInCaller"]
        if args.crash:
            os.kill(metrics["runnerPID"], signal.SIGTERM)

            async def failed():
                async for event in source.events():
                    if isinstance(event, SessionState) and event.state == "FAILED":
                        return event.model_dump(mode="json")

            result["terminatedChildState"] = await asyncio.wait_for(failed(), timeout=5)
    finally:
        await source.close()
    result["cleanup"] = source.stats()
    assert not result["cleanup"]["runnerAlive"]
    assert not result["cleanup"]["readerThreadsAlive"]
    return result


def main():
    parser = argparse.ArgumentParser(description="Real NewtonSource command and cleanup smoke")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--scene", default="falling-boxes")
    parser.add_argument("--crash", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(smoke(args))
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
