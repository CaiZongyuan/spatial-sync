"""The Source's typed digest must describe the same real work as client JSON."""

import hashlib
import json
import struct
from types import SimpleNamespace

import pytest
import yaml

from apps.api.app.sources.synthetic_source import REGISTRY, SyntheticSource
from packages.info.catalog import load_catalog
from packages.info.program import HostCore
from packages.protocol.schema import Actor, EntityObservations, InvokeRequest, ObservationChange
from tools import bench_info
from tools.bench_info import (
    CONTROL,
    PREFIX,
    ControllerProgram,
    TelemetryProgram,
    fixture,
    telemetry_rows,
    update_digest,
)


def test_typed_and_json_digests_match_ordered_goldens_from_actual_host_batches(tmp_path):
    root = fixture(tmp_path, 1000, 20, generation=60)
    registry = {**REGISTRY, ControllerProgram.id: ControllerProgram, TelemetryProgram.id: TelemetryProgram}
    catalog = load_catalog(SyntheticSource("digest-parity")._manifest(), "lab-demo", registry, root=root)
    clock = [0.0]
    host = HostCore(catalog, registry, publication_clock=lambda: clock[0])
    host.invoke(
        InvokeRequest(
            invocationId="enable",
            requestId="enable",
            entityKey=CONTROL,
            action="switch",
            params={"enabled": True},
            epoch=0,
            actor=Actor(kind="cli", name="test"),
            createdSimTime=0,
            createdAtMs=0,
        )
    )
    typed_digest, json_digest, expected_digest = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    changes = batches = 0

    def check(messages, tick):
        nonlocal changes, batches
        for message in messages:
            if not isinstance(message, EntityObservations):
                continue
            typed = list(telemetry_rows(message.changes))
            documents = list(telemetry_rows([change.model_dump(mode="json") for change in message.changes]))
            if not typed:
                assert not documents  # Initial controller publication precedes rate-limited telemetry.
                continue
            expected = [(index, tick + 1, tick) for index in range(1000)]
            assert typed == documents == expected
            changes += update_digest(typed_digest, typed)
            assert update_digest(json_digest, documents) == len(expected)
            for row in expected:
                expected_digest.update(struct.pack("<QQQ", *row))
            batches += 1

    for tick in range(1, 62):
        clock[0] = tick / 60
        check(host.tick(tick / 60, 1 / 60), tick)
    clock[0] += 1
    check(host.drain_events(), 61)
    assert changes == 21_000 and batches == 21
    assert typed_digest.digest() == json_digest.digest() == expected_digest.digest()


@pytest.mark.parametrize("value", [True, -1, 1.5])
@pytest.mark.parametrize("document", [True, False])
def test_source_and_client_counter_checks_still_reject_invalid_values(value, document):
    change = ObservationChange(key=PREFIX + "00000000", rev=1, values={"value": value})
    with pytest.raises(AssertionError, match="nonnegative integer"):
        list(telemetry_rows([change.model_dump() if document else change]))


class TelemetryContext:
    def __init__(self, rate):
        self.config = SimpleNamespace(controller=CONTROL, generationHz=rate)
        self.enabled, self.value, self.writes = True, None, []

    def read(self, key, prop):
        return self.enabled

    def get(self, prop):
        return self.value

    def set(self, *, value):
        self.value = value
        self.writes.append(value)


@pytest.mark.parametrize("rate", [5, 15, 60, 120])
def test_generation_frequency_counts_every_actual_write_at_the_configured_dt_rate(rate):
    program, ctx = TelemetryProgram(), TelemetryContext(rate)
    program.init(ctx)
    for _ in range(60):
        program.tick(ctx, 1 / 60)
    assert ctx.value == rate
    assert ctx.writes == list(range(rate + 1))


def test_disabled_generation_clears_partial_time_and_cannot_accumulate_a_reenable_burst():
    program, ctx = TelemetryProgram(), TelemetryContext(15)
    program.init(ctx)
    for _ in range(3):
        program.tick(ctx, 1 / 60)
    assert ctx.value == 0
    ctx.enabled = False
    for _ in range(120):
        program.tick(ctx, 1 / 60)
    assert ctx.writes == [0]
    ctx.enabled = True
    program.tick(ctx, 1 / 60)
    assert ctx.value == 0
    for _ in range(3):
        program.tick(ctx, 1 / 60)
    assert ctx.writes == [0, 1]


@pytest.mark.parametrize("generation", [None, 60])
def test_fixture_keeps_generation_configuration_separate_from_publication_limit(tmp_path, generation):
    root = fixture(tmp_path, 1, 15, generation)
    scene = yaml.safe_load((root / "scenes/lab-demo.yaml").read_text(encoding="utf-8"))
    entity = next(item for item in scene["entities"] if item.get("key", "").startswith(PREFIX))
    definition = yaml.safe_load((root / "types/bench-telemetry.v1.yaml").read_text(encoding="utf-8"))
    assert entity["config"]["generationHz"] == (15 if generation is None else 60)
    assert definition["properties"]["value"]["maxRateHz"] == 15


@pytest.mark.parametrize(
    "arrivals,expected_window",
    [([(at, at - 1) for at in range(1100, 2000, 100)] + [(2003, 1999)], 9000), ([(1003, 999)], 1000)],
)
async def test_reader_throughput_uses_receive_window_and_keeps_delayed_batches_in_complete_digest(
    monkeypatch, arrivals, expected_window
):
    clock = [1000]
    keys = [PREFIX + f"{index:08d}" for index in range(1000)]

    class Socket:
        async def recv(self):
            return json.dumps({"type": "server.ready", "protocolVersion": 2})

        async def send(self, message):
            pass

        async def __aiter__(self):
            yield json.dumps(
                {
                    "type": "entity.snapshot",
                    "sceneRevision": "revision",
                    "epoch": 0,
                    "sampleTimeMs": 1000,
                    "entities": [{"key": key} for key in keys],
                }
            )
            for value, (received, sampled) in enumerate(arrivals, 1):
                clock[0] = received
                yield json.dumps(
                    {
                        "type": "entity.observations",
                        "sceneRevision": "revision",
                        "epoch": 0,
                        "sampleTimeMs": sampled,
                        "changes": [
                            {"key": key, "rev": value + 1, "values": {"value": value}} for key in keys
                        ],
                    }
                )

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return Socket()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(bench_info, "connect", Connection)
    monkeypatch.setattr(bench_info.time, "monotonic", lambda: clock[0] / 1000)
    reader = bench_info.Reader(
        "ws://unused",
        {
            "sessionId": "reader-window",
            "sceneRevision": "revision",
            "revisionNumericId": 1,
            "nodes": [],
        },
        1000,
    )
    await reader.run()
    assert reader.error is None
    assert (
        sum(count for received, count in reader.change_samples if 1000 <= received <= 2000) == expected_window
    )
    assert reader.changes == len(arrivals) * 1000
    assert len(reader.latencies) == len(arrivals)
    assert reader.latencies[-1][0] == arrivals[-1][1]
    assert reader.latencies[-1][1] == pytest.approx(arrivals[-1][0] - arrivals[-1][1])
    expected_digest = hashlib.sha256()
    for value in range(1, len(arrivals) + 1):
        for index in range(1000):
            expected_digest.update(struct.pack("<QQQ", index, value + 1, value))
    assert reader.digest.digest() == expected_digest.digest()
