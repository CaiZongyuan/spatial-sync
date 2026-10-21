"""Three bounded-duration MQTT laboratory devices with explicit fault injection."""

import argparse
import heapq
import json
import math
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paho.mqtt.client as mqtt

from packages.live.bindings import Broker

TOPICS = {"incubator2": "lab/incubator2", "biosafety1": "lab/biosafety1",
          "environment1": "lab/environment1"}


class Devices:
    def __init__(self, *, ack_delay=0, drop_acks=False):
        self.lock = threading.Lock()
        self.ack_delay, self.drop_acks = ack_delay, drop_acks
        self.incubator = {"temp": 37, "co2": 5, "humidity": 60, "doorOpen": False, "setpoint": 37}
        self.cabinet = {"inflow": .5, "sashHeight": 0, "uvOn": False, "blowerOn": True}
        self.environment = {"temp": 22, "humidity": 40}
        self.disconnected = False
        self.completed = OrderedDict()
        self.pending_acks = []
        self.counter = 0

    def command(self, topic, payload):
        device = next((name for name, prefix in TOPICS.items() if topic == prefix + "/cmd"), None)
        if device is None or (device == "incubator2" and self.disconnected):
            return
        if (not isinstance(payload, dict) or set(payload) != {"requestId", "action", "params"}
                or not isinstance(payload["requestId"], str) or not payload["requestId"]
                or not isinstance(payload["params"], dict)):
            return
        identity, action, params = payload["requestId"], payload["action"], payload["params"]
        with self.lock:
            cached = self.completed.get((device, identity))
            if cached is None:
                valid = False
                if device == "incubator2" and action == "setSetpoint" and set(params) == {"temperatureC"}:
                    temperature = params["temperatureC"]
                    valid = (type(temperature) in (int, float) and math.isfinite(temperature)
                             and 20 <= temperature <= 60)
                    if valid:
                        self.incubator["setpoint"] = temperature
                elif device == "biosafety1" and action in ("setBlower", "setUv"):
                    valid = set(params) == {"on"} and type(params["on"]) is bool
                    if valid:
                        self.cabinet["blowerOn" if action == "setBlower" else "uvOn"] = params["on"]
                cached = {"requestId": identity, "status": "succeeded" if valid else "failed"}
                if not valid:
                    cached["error"] = "Unsupported mock command or invalid parameters"
                self.completed[device, identity] = cached
                while len(self.completed) > 128:
                    self.completed.popitem(last=False)
            if not self.drop_acks:
                self.counter += 1
                heapq.heappush(self.pending_acks, (time.monotonic() + self.ack_delay, self.counter,
                                                 TOPICS[device] + "/ack", cached))
                if len(self.pending_acks) > 128:
                    heapq.heappop(self.pending_acks)

    def samples(self, fault):
        with self.lock:
            self.disconnected = fault == "disconnect"
            self.incubator["temp"] = 45 if fault == "heat" else self.incubator["setpoint"]
            self.cabinet["inflow"] = .2 if fault == "low-airflow" else .5
            return {"incubator2": dict(self.incubator), "biosafety1": dict(self.cabinet),
                    "environment1": dict(self.environment)}

    def acknowledgements(self):
        with self.lock:
            result = []
            while self.pending_acks and self.pending_acks[0][0] <= time.monotonic():
                _, _, topic, payload = heapq.heappop(self.pending_acks)
                result.append((topic, payload))
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", default="mqtt://127.0.0.1:1883")
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--fault", choices=("heat", "disconnect", "low-airflow"))
    parser.add_argument("--after", type=float, default=5)
    parser.add_argument("--ack-delay", type=float, default=0)
    parser.add_argument("--drop-acks", action="store_true")
    args = parser.parse_args()
    if (not all(math.isfinite(value) for value in (args.seconds, args.after, args.ack_delay))
            or not 0 < args.seconds <= 3600 or min(args.after, args.ack_delay) < 0):
        parser.error("Use a duration in (0,3600] and nonnegative finite fault/ack delays")
    parsed = urlsplit(Broker(url=args.broker).url)
    devices = Devices(ack_delay=args.ack_delay, drop_acks=args.drop_acks)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="spatial-sync-mock-" + str(time.time_ns()))
    if parsed.username is not None:
        client.username_pw_set(unquote(parsed.username), unquote(parsed.password or ""))
    if parsed.scheme == "mqtts":
        client.tls_set()

    def connected(client, _userdata, _flags, reason, _properties):
        if not reason.is_failure:
            client.subscribe([(prefix + "/cmd", 0) for prefix in TOPICS.values()])

    def message(_client, _userdata, message):
        if not message.retain:
            try:
                devices.command(message.topic, json.loads(message.payload))
            except (ValueError, UnicodeError):
                pass

    client.on_connect, client.on_message = connected, message
    client.connect(parsed.hostname, parsed.port or (8883 if parsed.scheme == "mqtts" else 1883), keepalive=15)
    client.loop_start()
    started = time.monotonic()
    try:
        while time.monotonic() - started < args.seconds:
            fault = args.fault if time.monotonic() - started >= args.after else None
            for device, payload in devices.samples(fault).items():
                online = not (device == "incubator2" and fault == "disconnect")
                client.publish(TOPICS[device] + "/status", json.dumps({"online": online}), retain=True)
                if online:
                    client.publish(TOPICS[device] + "/telemetry", json.dumps(payload), retain=False)
            for topic, payload in devices.acknowledgements():
                client.publish(topic, json.dumps(payload), retain=False)
            time.sleep(.2)
    except KeyboardInterrupt:
        pass
    finally:
        for prefix in TOPICS.values():
            client.publish(prefix + "/status", '{"online":false}', retain=True)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
