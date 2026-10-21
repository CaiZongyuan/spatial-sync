"""Exercise the production Paho adapter against a socket-free SDK boundary."""

import json
from threading import Thread
from types import SimpleNamespace

import paho.mqtt.client as mqtt

from packages.live.bindings import Broker
from packages.live.mqtt import PahoTransport


class ClientBoundary:
    def __init__(self, *args, **kwargs):
        self.arguments, self.options = args, kwargs
        self.publications = []
        self.connected = True
        self.closed = False

    def connect_async(self, host, port, **kwargs):
        self.connection = host, port, kwargs

    def loop_start(self):
        return 0

    def subscribe(self, topics):
        self.subscriptions = topics
        return 0, 1

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, **kwargs):
        self.publications.append((topic, payload, kwargs))
        return SimpleNamespace(rc=0)

    def disconnect(self):
        self.connected = False
        self.on_disconnect(self, None, None, None, None)

    def loop_stop(self):
        self.closed = True


def test_actual_adapter_callbacks_queue_owned_bytes_and_bound_unsent_publications(monkeypatch):
    monkeypatch.setattr(mqtt, "Client", ClientBoundary)
    transport = PahoTransport(Broker(url="mqtt://localhost:18827"), ["telemetry", "ack"])
    transport.start()
    client = transport._client
    assert client.arguments == (mqtt.CallbackAPIVersion.VERSION2,)
    assert client.options == {"clean_session": True, "reconnect_on_failure": False}
    client.on_connect(client, None, None, SimpleNamespace(is_failure=False), None)
    assert client.subscriptions == [("telemetry", 0), ("ack", 0)]
    message = SimpleNamespace(topic="telemetry", payload=bytearray(b'{"temp":37}'), retain=False)
    thread = Thread(target=client.on_message, args=(client, None, message))
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    message.payload[:] = b"changed"
    connected, received = transport.ingress.drain()
    assert connected.kind == "connected" and received.payload == b'{"temp":37}'
    payload = {"requestId": "inv-one", "action": "setSetpoint", "params": {"temperatureC": 38}}
    for _ in range(128):
        assert transport.publish("cmd", payload)
    assert not transport.publish("cmd", payload)
    assert all(options == {"qos": 0, "retain": False} for _, _, options in client.publications)
    assert json.loads(client.publications[0][1]) == payload
    client.on_publish(client, None, 1, None, None)
    assert transport.publish("cmd", payload)
    transport.close()
    assert client.closed and not transport.publish("cmd", payload)
    client.on_message(client, None, message)
    assert transport.ingress.drain() == ()
