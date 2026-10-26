"""Deterministic external-process stand-in; never calls a model/provider."""

import argparse
import json
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=Path)
    parser.add_argument("--delay", type=float, default=0)
    args = parser.parse_args()
    request = json.load(sys.stdin)
    if args.calls:
        with args.calls.open("a", encoding="utf-8") as output:
            output.write(request["batchId"] + "\n")
    if args.delay:
        time.sleep(args.delay)
    results = [
        {
            "upstreamId": entry["upstreamId"],
            "inputDigest": entry["inputDigest"],
            "fields": {
                "name": {"en": "Fixture " + entry["upstreamId"], "zh": "夹具 " + entry["upstreamId"]},
                "summary": {"en": "Deterministic process fixture", "zh": "确定性进程夹具"},
                "category": "container",
                "tags": ["fixture", "夹具"],
            },
        }
        for entry in request["entries"]
    ]
    print(
        json.dumps(
            {
                "results": results,
                "nativeSessionId": "fixture-" + request["batchId"],
                "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
            }
        )
    )


if __name__ == "__main__":
    main()
