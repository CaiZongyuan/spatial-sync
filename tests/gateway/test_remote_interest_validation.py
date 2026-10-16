"""Authenticated interest requests remain bounded before reaching Source authority."""

import asyncio
import json

import pytest
from test_remote_runner import raw_pair
from test_remote_runner import tls_material as tls_material

from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.transport.remote import JSON, Limits, ServerConfig, close_stream, send
from packages.transport.remote_server import RunnerServer


@pytest.mark.parametrize(
    "malformed",
    ["missing", "duplicate", "boolean", "negative", "overflow", "text", "unknown", "too-many", "wrong-kind"],
)
async def test_actual_tls_invalid_interest_never_reaches_source_authority(tls_material, malformed):
    cert, key, token = tls_material
    calls, closed = [], asyncio.Event()

    class TrackedSynthetic(SyntheticSource):
        async def set_interest(self, stream_ids):
            calls.append(stream_ids)
            return await super().set_interest(stream_ids)

        async def close(self):
            await super().close()
            closed.set()

    limits = Limits(heartbeat_seconds=0.05, read_seconds=0.8, close_seconds=0.2)
    async with RunnerServer(
        ServerConfig(
            cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic", limits=limits
        ), source_factory=TrackedSynthetic,
    ) as server:
        control, writer, _, frame_writer, _ = await raw_pair(server, cert, token)
        try:
            packet = {"kind": "interest", "token": "invalid-interest"}
            if malformed != "missing":
                packet["streamIds"] = {
                    "duplicate": [1, 1], "boolean": [True], "negative": [-1],
                    "overflow": [4294967296], "text": ["1"], "unknown": [4294967295],
                    "too-many": list(range(131073)), "wrong-kind": [1],
                }[malformed]
            if malformed == "wrong-kind":
                packet["kind"] = "snapshot"
            await send(writer, JSON, json.dumps(packet).encode(), limits)
            await asyncio.wait_for(closed.wait(), 1)
            await asyncio.wait_for(control.read(), 1)
            assert control.at_eof() and calls == []
        finally:
            await asyncio.gather(close_stream(writer, 1), close_stream(frame_writer, 1))
    assert server.active_sessions == 0
