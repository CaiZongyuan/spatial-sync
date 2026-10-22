"""L3 browser fixture: actual DeviceHost and programs with socket-free device peers."""

import asyncio
import json
import os
import threading
import time
from collections import deque
from copy import deepcopy
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from apps.api.app.main import create_app
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system
from apps.api.app.settings import Settings
from packages.live.ingress import BrokerIngress

ROOT = Path(__file__).resolve().parents[2]
OWNED = ROOT / ".scratch/live-operations-e2e"
for variable, directory in (("LW_LIBRARY", "library"), ("SPATIAL_STATE_ROOT", "state"),
                            ("SPATIAL_RECORDINGS_ROOT", "recordings")):
    os.environ.setdefault(variable, str(OWNED / directory))


class Devices:
    def __init__(self, _broker, _topics):
        self.ingress = BrokerIngress()
        self.closed = False
        self.mode = "normal"
        self.setpoint = 37
        self.commands = deque(maxlen=100)
        self.done = threading.Event()

    def emit(self, topic, value):
        self.ingress.message(topic, json.dumps(value).encode())

    def start(self):
        self.loop = asyncio.get_running_loop()
        self.ingress.connected()
        self.task = asyncio.create_task(self.run(), name="live-browser-device-peers")

    async def run(self):
        try:
            while not self.closed:
                if self.mode != "quiet":
                    self.emit("lab/incubator2/telemetry", {
                        "temp": self.setpoint + (4 if self.mode == "heat" else 0), "setpoint": self.setpoint,
                        "co2": 5, "humidity": 60, "doorOpen": False,
                    })
                self.emit("lab/biosafety1/telemetry", {"inflow": .5, "sashHeight": 0,
                                                      "uvOn": False, "blowerOn": False})
                self.emit("lab/environment1/telemetry", {"temp": 22, "humidity": 45})
                await asyncio.sleep(.2)
        finally:
            self.done.set()

    def publish(self, topic, payload):
        if self.closed:
            return False
        self.commands.append({"topic": topic, "payload": deepcopy(payload), "at": time.monotonic()})
        if topic == "lab/incubator2/cmd":
            self.setpoint = payload["params"]["temperatureC"]
            self.emit("lab/incubator2/ack", {"requestId": payload["requestId"], "status": "succeeded"})
        elif topic == "lab/biosafety1/cmd":
            self.emit("lab/biosafety1/ack", {"requestId": payload["requestId"], "status": "succeeded"})
        return True

    def close(self):
        self.closed = True
        self.ingress.close()
        self.loop.call_soon_threadsafe(self.task.cancel)
        if not self.done.wait(3):
            raise OSError("Fixture device peers did not stop")


transports, source_calls = [], []


def transport_factory(broker, topics):
    peer = Devices(broker, topics)
    transports.append(peer)
    return peer


def forbidden_source(*args):
    source_calls.append(args)
    raise AssertionError("Real-device UI must not create a spatial Source")


class Sampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Host probes disabled in the Live browser fixture"))


bindings = yaml.safe_load((ROOT / "devices/live/lab.yaml").read_text(encoding="utf-8"))
if os.getenv("SPATIAL_TEST_LIVE_LAYOUT") == "1":
    bindings["layout"] = "lab-demo@2"
    for entity, place in zip(bindings["entities"], (
        "/World/Lab/Incubator", "/World/Lab/BiosafetyCabinet", "/World/Lab/EnvironmentSensor",
    ), strict=True):
        entity["placeAt"] = place
bindings["entities"][0]["alerts"] = {"tempDeviation": {
    "when": {"property": "temperatureC", "deviatesFrom": "setpointC", "by": .5},
    "forSeconds": .25, "severity": "critical", "message": "培养箱温度偏离设定值",
    "hint": "核对设定值与门状态。",
}}
config = Path(os.environ["SPATIAL_STATE_ROOT"]) / "fixture-live.yaml"
config.parent.mkdir(parents=True, exist_ok=True)
config.write_text(yaml.safe_dump(bindings), encoding="utf-8")
settings = Settings(live_bindings=config,
                    allowed_origins=tuple(os.getenv("SPATIAL_ALLOWED_ORIGINS", "http://127.0.0.1:5184").split(",")))
app = create_app(settings, source_factory=forbidden_source, live_transport_factory=transport_factory,
                 platform_sampler=Sampler())


class Fault(BaseModel):
    mode: Literal["normal", "heat", "quiet"]


@app.get("/e2e/storage-roots")
async def storage_roots():
    return {"sceneLibrary": str(app.state.manager.library.roots["user"]),
            "assetLibrary": str(app.state.library_assets.roots["user"]),
            "state": str(settings.state_root.resolve()),
            "recordings": str(settings.recordings_root.resolve())}


@app.get("/e2e/live")
async def live_receipt():
    return {"sourceCalls": len(source_calls),
            "commands": [item for peer in transports for item in peer.commands],
            "peerTasksDone": all(peer.done.is_set() for peer in transports),
            "sessions": len(app.state.manager.sessions)}


@app.post("/e2e/live/fault")
async def fault(body: Fault):
    for peer in transports:
        peer.mode = body.mode
    return {"mode": body.mode}


@app.post("/e2e/live/close")
async def close_live():
    await app.state.manager.live.close()
    return await live_receipt()
