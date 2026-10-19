from collections.abc import AsyncIterator
from typing import Protocol

from packages.info.catalog import Catalog
from packages.protocol.internal import CommandResult, JointState, SessionOptions
from packages.protocol.schema import (
    Command,
    EntityEvent,
    EntityObservations,
    EntitySnapshot,
    InvocationUpdate,
    InvokeRequest,
    SceneManifest,
    SceneReplaced,
    ServerError,
    SessionState,
    TransformFrame,
)

SourceEvent = (
    SessionState
    | SceneReplaced
    | ServerError
    | EntitySnapshot
    | EntityObservations
    | EntityEvent
    | InvocationUpdate
)


class SpatialSource(Protocol):
    async def start(self, scene_id: str, options: SessionOptions) -> tuple[SceneManifest, Catalog]: ...
    def entity_snapshot(self) -> EntitySnapshot: ...
    async def invoke(self, request: InvokeRequest) -> InvocationUpdate:
        """Queue intake observations/events then acceptance on the reliable stream before replying."""
        ...

    async def cancel(self, invocation_id: str) -> InvocationUpdate: ...
    async def revoke_before_start(self, invocation_id: str) -> InvocationUpdate: ...
    def latest_frame(self) -> TransformFrame | None: ...
    async def snapshot(self) -> TransformFrame: ...
    def events(self) -> AsyncIterator[SourceEvent]: ...
    async def execute(self, command: Command) -> CommandResult: ...
    async def query_joint_state(self, joint_key: str) -> JointState: ...
    async def read_mesh(self, mesh_id: str) -> bytes: ...
    async def close(self) -> None: ...
