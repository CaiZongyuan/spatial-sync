"""Client callbacks hand off bounded bytes and receipt time, never device state."""

from packages.live.ingress import BrokerIngress


def test_receive_owns_bytes_and_records_arrival_before_gateway_processing():
    now = [1.0]
    ingress = BrokerIngress(clock=lambda: now[0])
    ingress.connected()
    value = bytearray(b'{"temp":37}')
    now[0] = 2
    assert ingress.message("telemetry", value)
    value[:] = b"mutated"
    now[0] = 10
    connected, sample = ingress.drain()
    assert connected.generation == sample.generation == 1
    assert sample.received_at == 2
    assert sample.payload == b'{"temp":37}'
    assert ingress.drain() == ()


def test_overflow_discards_partial_stream_and_requires_reconnection():
    ingress = BrokerIngress(capacity=1, clock=lambda: 1)
    ingress.connected()
    assert not ingress.message("status", b'{"online":false}')
    (receipt,) = ingress.drain()
    assert receipt.kind == "disconnected"
    assert not ingress.message("telemetry", b"{}")
    ingress.connected()
    (receipt,) = ingress.drain()
    assert receipt.kind == "connected" and receipt.generation == 2
    assert ingress.message("telemetry", b"{}")


def test_disconnect_and_shutdown_reject_late_callbacks_and_oversize_payload():
    ingress = BrokerIngress(max_payload=2)
    assert not ingress.message("telemetry", b"{}")
    ingress.connected()
    assert not ingress.message("telemetry", b"123")
    ingress.disconnected()
    assert not ingress.message("ack", b"{}")
    assert [receipt.kind for receipt in ingress.drain()] == ["connected", "disconnected"]
    ingress.close()
    ingress.connected()
    assert not ingress.message("telemetry", b"{}")
    assert ingress.drain() == ()
