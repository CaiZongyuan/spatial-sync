"""Versioned, bounded TLS wire values. No Python object deserialization crosses this boundary."""

import asyncio
import json
import math
import os
import ssl
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, TypeAdapter, model_validator

from packages.protocol.schema import (
    Command,
    CommandResult,
    EntityState,
    JointState,
    JsonValue,
    Model,
    SceneManifest,
    SceneReplaced,
    ServerError,
    SessionOptions,
    SessionState,
)

PACKET_HEADER = struct.Struct("!BI")
JSON, FRAME, MESH = 1, 2, 3
Token = Annotated[str, Field(strict=True, min_length=1, max_length=128)]


@dataclass(frozen=True)
class Limits:
    control_bytes: int = 4 * 1024 * 1024
    frame_bytes: int = 4 * 1024 * 1024
    mesh_bytes: int = 32 * 1024 * 1024
    pending_bytes: int = 64 * 1024 * 1024
    pending_messages: int = 128
    handshake_seconds: float = 5
    read_seconds: float = 10
    request_seconds: float = 5
    start_seconds: float = 180
    close_seconds: float = 3
    heartbeat_seconds: float = 1

    def __post_init__(self):
        for value in vars(self).values():
            if not math.isfinite(value) or value <= 0:
                raise ValueError("remote limits must be positive and finite")
        if self.read_seconds <= self.heartbeat_seconds:
            raise ValueError("read timeout must exceed the heartbeat interval")

    def maximum(self, kind):
        if kind not in (JSON, FRAME, MESH):
            raise ValueError("unknown remote packet kind")
        return {JSON: self.control_bytes, FRAME: self.frame_bytes, MESH: self.mesh_bytes}[kind]


@dataclass(frozen=True)
class RemoteConfig:
    ca_file: Path
    token: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 8766
    server_name: str = "localhost"
    source_kind: Literal["newton", "synthetic"] = "newton"
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self):
        _validate_config(self.token, self.source_kind)
        if not self.host or not self.server_name or not 1 <= self.port <= 65535:
            raise ValueError("remote endpoint and certificate server name are required")

    def tls_context(self):
        context = ssl.create_default_context(cafile=str(self.ca_file))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    @classmethod
    def from_env(cls):
        names = (
            "SPATIAL_RUNNER_HOST",
            "SPATIAL_RUNNER_PORT",
            "SPATIAL_RUNNER_CA_FILE",
            "SPATIAL_RUNNER_TOKEN",
            "SPATIAL_RUNNER_KIND",
            "SPATIAL_RUNNER_SERVER_NAME",
        )
        if not any(name in os.environ for name in names):
            return None
        if not os.getenv("SPATIAL_RUNNER_CA_FILE") or not os.getenv("SPATIAL_RUNNER_TOKEN"):
            raise ValueError("remote Runner configuration requires a CA file and token")
        return cls(
            ca_file=Path(os.environ["SPATIAL_RUNNER_CA_FILE"]),
            token=os.environ["SPATIAL_RUNNER_TOKEN"],
            host=os.getenv("SPATIAL_RUNNER_HOST", "127.0.0.1"),
            port=int(os.getenv("SPATIAL_RUNNER_PORT", "8766")),
            source_kind=os.getenv("SPATIAL_RUNNER_KIND", "newton"),
            server_name=os.getenv("SPATIAL_RUNNER_SERVER_NAME", "localhost"),
        )


@dataclass(frozen=True)
class ServerConfig:
    cert_file: Path
    key_file: Path = field(repr=False)
    token: str = field(repr=False)
    host: str = "127.0.0.1"
    port: int = 8766
    source_kind: Literal["newton", "synthetic"] = "newton"
    device: Literal["cpu", "cuda:0"] = "cpu"
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self):
        _validate_config(self.token, self.source_kind)
        if not self.host or not 0 <= self.port <= 65535:
            raise ValueError("invalid runner bind endpoint")

    def tls_context(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(self.cert_file), str(self.key_file))
        return context


def _validate_config(token, source_kind):
    if not isinstance(token, str) or not 32 <= len(token) <= 512:
        raise ValueError("runner token must contain 32–512 characters")
    if source_kind not in ("newton", "synthetic"):
        raise ValueError("unsupported fixed runner source")


class Hello(Model):
    kind: Literal["hello"] = "hello"
    version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    role: Literal["control", "frames"]
    token: Annotated[str, Field(strict=True, min_length=32, max_length=512, repr=False)]
    ticket: Token | None = None


class Authenticated(Model):
    kind: Literal["authenticated"] = "authenticated"
    ticket: Token


class Attached(Model):
    kind: Literal["attached"] = "attached"


class Start(Model):
    kind: Literal["start"] = "start"
    sessionId: Token
    sceneId: Token
    options: SessionOptions


class Request(Model):
    kind: Literal["command", "joint", "mesh", "close", "ping"]
    token: Token
    command: Command | None = None
    key: Annotated[str, Field(strict=True, min_length=1, max_length=2048)] | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        if (self.kind == "command") != (self.command is not None):
            raise ValueError("command payload does not match operation")
        if (self.kind in ("joint", "mesh")) != (self.key is not None):
            raise ValueError("key payload does not match operation")
        if self.command and len(self.command.requestId) > 128:
            raise ValueError("remote command requestId exceeds its bound")
        return self


class Ready(Model):
    kind: Literal["ready"] = "ready"
    manifest: SceneManifest
    stats: dict[str, JsonValue] = Field(default_factory=dict)


class Result(Model):
    kind: Literal["result"] = "result"
    token: Token
    result: CommandResult
    hasFrame: StrictBool


class JointResult(Model):
    kind: Literal["joint_result"] = "joint_result"
    token: Token
    state: JointState


class MeshResult(Model):
    kind: Literal["mesh_result"] = "mesh_result"
    token: Token


class Unknown(Model):
    kind: Literal["unknown_entity"] = "unknown_entity"
    token: Token
    key: str


class Closed(Model):
    kind: Literal["closed"] = "closed"
    token: Token


class QueryTimeout(Model):
    kind: Literal["query_timeout"] = "query_timeout"
    token: Token


class Heartbeat(Model):
    kind: Literal["heartbeat"] = "heartbeat"
    stats: dict[str, JsonValue] = Field(default_factory=dict)


class Event(Model):
    kind: Literal["event"] = "event"
    event: Annotated[SessionState | SceneReplaced | EntityState | ServerError, Field(discriminator="type")]


class Failed(Model):
    kind: Literal["failed"] = "failed"
    message: Annotated[str, Field(strict=True, min_length=1, max_length=2048)]


PACKET = TypeAdapter(
    Annotated[
        Hello
        | Authenticated
        | Attached
        | Start
        | Request
        | Ready
        | Result
        | JointResult
        | MeshResult
        | Unknown
        | Closed
        | QueryTimeout
        | Heartbeat
        | Event
        | Failed,
        Field(discriminator="kind"),
    ]
)


def json_bytes(value: Model):
    return json.dumps(
        value.model_dump(mode="json", exclude_none=True),
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def parse_json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate remote JSON key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("nonfinite remote JSON number")

    return PACKET.validate_python(
        json.loads(payload, object_pairs_hook=pairs, parse_constant=invalid_constant)
    )


async def receive(reader, limits, *, timeout=None, allowed=(JSON, FRAME, MESH)):
    async with asyncio.timeout(timeout if timeout is not None else limits.read_seconds):
        kind, length = PACKET_HEADER.unpack(await reader.readexactly(PACKET_HEADER.size))
        if kind not in allowed or length > limits.maximum(kind) or length == 0:
            raise ValueError("invalid remote packet kind or bounded length")
        # Validate announced size before readexactly can allocate its payload.
        return kind, await reader.readexactly(length)


async def send(writer, kind, payload, limits):
    if not payload or len(payload) > limits.maximum(kind):
        raise ValueError("remote outbound packet exceeds bounded length")
    writer.write(PACKET_HEADER.pack(kind, len(payload)))
    writer.write(payload)
    await asyncio.wait_for(writer.drain(), limits.request_seconds)


async def close_stream(writer, timeout):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout)
    except (OSError, TimeoutError):
        writer.transport.abort()


class Outbox:
    """One writer owns a channel; a reliable batch cannot be interleaved by heartbeats."""

    def __init__(self, writer, limits, *, latest=False):
        self.writer, self.limits, self.latest = writer, limits, latest
        self.queue = asyncio.Queue(maxsize=1 if latest else limits.pending_messages)
        self.bytes = 0
        self.dropped = 0
        self.task = asyncio.create_task(self._run(), name="remote-channel-writer")

    def offer(self, *packets):
        batch = tuple(packets)
        size = sum(len(payload) + PACKET_HEADER.size for _, payload in batch)
        for kind, payload in batch:
            if not payload or len(payload) > self.limits.maximum(kind):
                raise ValueError("remote packet exceeds bounded length")
        if self.task.done():
            raise ConnectionError("remote channel writer stopped")
        if self.latest and self.queue.full():
            old_size, _ = self.queue.get_nowait()
            self.bytes -= old_size
            self.dropped += 1
        if self.bytes + size > self.limits.pending_bytes:
            raise ConnectionError("remote pending byte budget exceeded")
        self.queue.put_nowait((size, batch))
        self.bytes += size

    async def _run(self):
        while True:
            size, batch = await self.queue.get()
            try:
                for kind, payload in batch:
                    await send(self.writer, kind, payload, self.limits)
            finally:
                self.bytes -= size
                self.queue.task_done()

    async def close(self, *, drain=False):
        if drain and not self.task.done():
            try:
                await asyncio.wait_for(self.queue.join(), self.limits.close_seconds)
            except TimeoutError:
                pass
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
