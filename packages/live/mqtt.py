"""Paho MQTT transport; network callbacks own only bounded transport state."""

import json
from threading import Lock
from urllib.parse import unquote, urlsplit

from packages.live.ingress import BrokerIngress


class PahoTransport:
    """One client per connection attempt; a disconnected client is never reused.

    Discarding the old client also discards its unsent network packets. Automatic
    reconnect and retained commands are disabled, so commands cannot replay on a
    replacement connection. DeviceHost owns when to close and replace this object.
    """

    def __init__(self, broker, topics):
        self.broker, self.topics = broker, tuple(topics)
        self.ingress = BrokerIngress()
        self._client = None
        self._pending_lock = Lock()
        self._pending_publications = 0
        self._closing = False

    def start(self):
        # Disabled Live configurations never import an MQTT SDK or open a socket.
        import paho.mqtt.client as mqtt

        parsed = urlsplit(self.broker.url)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, clean_session=True,
                             reconnect_on_failure=False)
        self._client = client
        if parsed.username is not None:
            client.username_pw_set(unquote(parsed.username),
                                   unquote(parsed.password) if parsed.password is not None else None)
        if parsed.scheme == "mqtts":
            client.tls_set()
        client.on_connect = self._connected
        client.on_connect_fail = lambda _client, _userdata: self.ingress.disconnected()
        client.on_disconnect = lambda *_args: self.ingress.disconnected()
        client.on_message = lambda _client, _userdata, message: self.ingress.message(
            message.topic, message.payload, retained=message.retain,
        )
        client.on_publish = self._published
        client.connect_async(parsed.hostname, parsed.port or (8883 if parsed.scheme == "mqtts" else 1883),
                             keepalive=15)
        if client.loop_start() != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError("MQTT network worker could not start")

    def _connected(self, client, _userdata, _flags, reason_code, _properties):
        if reason_code.is_failure:
            self.ingress.disconnected()
            return
        result, _mid = client.subscribe([(topic, 0) for topic in self.topics])
        if result != 0:
            self.ingress.disconnected()
            return
        self.ingress.connected()

    def _published(self, *_args):
        with self._pending_lock:
            self._pending_publications = max(0, self._pending_publications - 1)

    def publish(self, topic, payload):
        client = self._client
        if self._closing or client is None or not client.is_connected():
            return False
        with self._pending_lock:
            if self._pending_publications >= 128:
                return False
            self._pending_publications += 1
        try:
            result = client.publish(topic, json.dumps(payload, allow_nan=False, separators=(",", ":")),
                                    qos=0, retain=False)
        except (ValueError, TypeError, RuntimeError, OSError):
            self._published()
            return False
        if result.rc != 0:
            self._published()
            return False
        return True

    def close(self):
        self._closing = True
        self.ingress.close()
        client = self._client
        if client is not None:
            try:
                client.disconnect()
            finally:
                client.loop_stop()
        self._client = None
