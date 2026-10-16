"""Explicit TLS Runner host; token comes from an environment variable, never a CLI argument."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.transport.remote import Limits, ServerConfig
from packages.transport.remote_server import RunnerServer


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--token-env", default="SPATIAL_RUNNER_TOKEN")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--source", choices=("newton", "synthetic"), default="newton")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--request-timeout", type=float, default=5)
    parser.add_argument("--start-timeout", type=float, default=180)
    parser.add_argument("--read-timeout", type=float, default=10)
    parser.add_argument("--max-frame-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--max-control-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--max-mesh-bytes", type=int, default=32 * 1024 * 1024)
    return parser.parse_args()


async def main(args):
    config = ServerConfig(
        cert_file=args.cert,
        key_file=args.key,
        token=os.getenv(args.token_env, ""),
        host=args.host,
        port=args.port,
        source_kind=args.source,
        device=args.device,
        limits=Limits(
            request_seconds=args.request_timeout,
            start_seconds=args.start_timeout,
            read_seconds=args.read_timeout,
            frame_bytes=args.max_frame_bytes,
            control_bytes=args.max_control_bytes,
            mesh_bytes=args.max_mesh_bytes,
        ),
    )
    async with RunnerServer(config) as server:
        print(
            json.dumps(
                {
                    "event": "remote.runner.listening",
                    "host": config.host,
                    "port": server.port,
                    "source": config.source_kind,
                    "device": config.device,
                    "tls": "required",
                    "maxSessions": 1,
                    "limits": vars(config.limits),
                }
            ),
            flush=True,
        )
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main(arguments()))
    except KeyboardInterrupt:
        pass
