"""A GPU-free controlled producer using only the public authenticated Node endpoints.

Stdin commands emit complete test poses. This fixture never imports a physics SDK.
"""

import argparse
import hashlib
import json
import sys

import httpx
from websockets.sync.client import connect

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("job")
parser.add_argument("lease")
parser.add_argument("token")
parser.add_argument("instance")
args = parser.parse_args()
headers = {"X-Node-Token": args.token, "X-Node-Instance": args.instance, "X-Lease-Id": args.lease}
with httpx.Client(base_url=args.url, headers=headers, trust_env=False) as http:
    response = http.get(f"/api/v1/jobs/{args.job}/inputs")
    response.raise_for_status()
    inputs = response.json()
job, hint = inputs["job"], inputs["view"]
assert "error" not in hint, hint
stream, view_id, epochs = None, None, [0] * 4


def start(incarnation):
    global stream, view_id, epochs
    if stream is not None:
        stream.close()
    epochs = [0] * 4
    view_id = f"fake-view-{incarnation}"
    stream = connect(
        args.url.replace("http", "ws", 1) + f"/api/v1/jobs/{args.job}/view-frames",
        additional_headers=headers, proxy=None, open_timeout=3, close_timeout=1,
    )
    source = {"kind": "newton", "newtonVersion": "GPU-free controlled transport fixture",
              "solver": "No physics is executed", "physicsDt": 1 / 60, "basePublishHz": 10.0,
              "realtimeFactor": None, "sampleTimeClock": "runner-monotonic"}
    stream.send(json.dumps(envelope("hello", {
        **{key: job[key] for key in ("task", "taskContentHash", "scene", "sceneContentHash", "assets")},
        "descriptorDigest": hint["descriptorDigest"], "mappingDigest": hashlib.sha256(b"fixture").hexdigest(),
        "environments": [0, 1, 2, 3], "source": source, "maxRateHz": 10.0,
    })))


def envelope(kind, payload):
    return {"schema": "lw.training-view/1", "type": kind, "jobId": args.job,
            "attemptId": hint["attemptId"], "viewId": view_id, "payload": payload}


try:
    start(1)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        command = json.loads(line)
        if command["type"] == "restart":
            start(command["incarnation"])
        else:
            seq = command["seq"]
            if command.get("reset") is not None:
                epochs[command["reset"]] += 1
            for index in range(4):
                frame = envelope("frame", {
                    "protocolVersion": 2, "type": "stream.snapshot",
                    "sessionId": f"training-view:{view_id}:env:{index}",
                    "sceneRevision": "training-scene-" + hint["descriptorDigest"],
                    "seq": seq, "epoch": epochs[index], "simTime": seq / 10, "sampleTimeMs": 1000 + seq * 100,
                    "poses": [[row["streamId"], index * 0.1, number * 0.002, 1, 0, 0, 0, 1]
                              for number, row in enumerate(hint["bodyTable"])],
                })
                frame["envIndex"] = index
                stream.send(json.dumps(frame))
        print(json.dumps({"sent": command}), flush=True)
finally:
    if stream is not None:
        stream.close()
