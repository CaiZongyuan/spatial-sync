"""The Source's typed digest must describe the same real work as client JSON."""

import hashlib
import struct

import pytest

from apps.api.app.sources.synthetic_source import REGISTRY, SyntheticSource
from packages.info.catalog import load_catalog
from packages.info.program import HostCore
from packages.protocol.schema import Actor, EntityObservations, InvokeRequest, ObservationChange
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
    root = fixture(tmp_path, 1000, 20)
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
