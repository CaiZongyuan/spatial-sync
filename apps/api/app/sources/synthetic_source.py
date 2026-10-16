"""Deterministic kinematic scene for GPU-free gateway, viewer and CI work."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
import uuid
from collections.abc import AsyncIterator

from packages.protocol.internal import CommandResult, JointState, SessionOptions
from packages.protocol.math import compose_pose
from packages.protocol.schema import (
    Command,
    CommandAck,
    EntityState,
    SceneManifest,
    SessionState,
    TransformFrame,
    validate_command_args,
)

IDENTITY = {"p": [0, 0, 0], "q": [0, 0, 0, 1]}
BASE = "/World/Lab"
LINK1, LINK2 = BASE + "/Robot/Link1", BASE + "/Robot/Link2"
JOINT1, JOINT2 = BASE + "/Robot/Joint1", BASE + "/Robot/Joint2"


def _primitive(geometry, p=(0, 0, 0)):
    return {"kind": "primitive", "geometry": geometry, "localPose": {"p": p, "q": [0, 0, 0, 1]}}


class SyntheticSource:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.options = SessionOptions()
        self._task: asyncio.Task | None = None
        self._events = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._step_index = 0
        self._seq = 0
        self._epoch = 0
        self._state = "CREATED"
        self._latest: TransformFrame | None = None
        self._last_clock = time.monotonic()
        self._accumulator = 0.0
        self._actual_rtf = 0.0

    async def start(self, scene_id: str, options: SessionOptions) -> SceneManifest:
        if scene_id != "lab-demo":
            raise ValueError("unknown synthetic scene")
        self.options = options.model_copy()
        self.manifest = self._manifest()
        self._state = "RUNNING"
        self._publish("stream.snapshot")
        self._last_clock = time.monotonic()
        self._task = asyncio.create_task(self._run(), name=f"synthetic-{self.session_id}")
        self._events.put_nowait(
            EntityState(
                sessionId=self.session_id,
                sceneRevision=self.manifest.sceneRevision,
                entityKey=BASE + "/Centrifuge",
                schema="device.centrifuge/0",
                stateSeq=1,
                simTime=0,
                payload={"running": True, "lidOpen": False, "rpm": 1200},
            )
        )
        return self.manifest

    @property
    def sim_time(self) -> float:
        return self._step_index * self.options.physicsDt

    def latest_frame(self) -> TransformFrame | None:
        """Published objects are replaced as a whole and never subsequently mutated."""
        return self._latest

    async def events(self) -> AsyncIterator:
        while True:
            event = await self._events.get()
            if event is None:
                break
            yield event

    async def _run(self):
        next_publish = time.monotonic() + 1 / self.options.basePublishHz
        while True:
            await asyncio.sleep(min(self.options.physicsDt, 0.01))
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_clock
                self._last_clock = now
                if self._state != "RUNNING":
                    self._actual_rtf = 0.0
                    continue
                rtf = self.options.realtimeFactor
                if rtf is None:
                    steps = max(1, int((1 / self.options.basePublishHz) / self.options.physicsDt))
                else:
                    self._accumulator += min(elapsed, self.options.maxLagMs / 1000) * rtf
                    steps = int(self._accumulator / self.options.physicsDt)
                    self._accumulator -= steps * self.options.physicsDt
                self._step_index += steps
                self._actual_rtf = steps * self.options.physicsDt / elapsed if elapsed > 0 else 0.0
                if now >= next_publish:
                    self._publish()
                    next_publish = now + 1 / self.options.basePublishHz

    def _publish(self, kind="transform.frame") -> TransformFrame:
        self._seq += 1
        self._latest = TransformFrame(
            type=kind,
            sessionId=self.session_id,
            sceneRevision=self.manifest.sceneRevision,
            epoch=self._epoch,
            seq=self._seq,
            simTime=self.sim_time,
            sampleTimeMs=time.monotonic() * 1000,
            poses=self._poses(),
        )
        return self._latest

    def _poses(self):
        t = self.sim_time
        a, b = 0.45 * math.sin(t), 0.6 * math.sin(t * 0.7 + 0.5)
        first = {"p": [0, 0, 0.65], "q": [0, math.sin(a / 2), 0, math.cos(a / 2)]}
        second = compose_pose(first, {"p": [0, 0, 0.6], "q": [0, math.sin(b / 2), 0, math.cos(b / 2)]})
        poses = [(1, *first["p"], *first["q"]), (2, *second["p"], *second["q"])]
        for index in range(20):
            x = 0.8 + (index % 5) * 0.16
            y = -0.4 + (index // 5) * 0.2
            z = 0.91 + 0.06 * math.sin(t * 1.2 + index * 0.4)
            poses.append((10 + index, x, y, z, 0, 0, 0, 1))
        poses += [
            (30, -0.9, 0.4, 0.85, 0, 0, math.sin(t), math.cos(t)),
            (31, -0.7, -0.6, 0.5 + 0.15 * math.sin(t), 0, 0, 0, 1),
        ]
        return poses

    def _manifest(self):
        nodes = [
            {"kind": "group", "entityKey": "/World", "name": "World"},
            {"kind": "group", "entityKey": BASE, "name": "Lab", "parentEntityKey": "/World"},
            {
                "kind": "group",
                "entityKey": BASE + "/Robot",
                "name": "Two-link Robot",
                "parentEntityKey": BASE,
            },
            {"kind": "group", "entityKey": BASE + "/Tubes", "name": "20 Tubes", "parentEntityKey": BASE},
            {
                "kind": "group",
                "entityKey": BASE + "/Centrifuge",
                "name": "Centrifuge",
                "parentEntityKey": BASE,
            },
            {
                "kind": "static",
                "entityKey": BASE + "/Floor",
                "name": "Floor",
                "parentEntityKey": BASE,
                "initialPose": IDENTITY,
                "visuals": [_primitive({"type": "plane", "width": 10, "length": 10})],
            },
            {
                "kind": "static",
                "entityKey": BASE + "/Table",
                "name": "Workbench",
                "parentEntityKey": BASE,
                "initialPose": {"p": [0.7, 0, 0.35], "q": [0, 0, 0, 1]},
                "visuals": [_primitive({"type": "box", "halfExtents": [1.3, 0.8, 0.05]})],
            },
            {
                "kind": "body",
                "entityKey": LINK1,
                "name": "Link1",
                "parentEntityKey": BASE + "/Robot",
                "streamId": 1,
                "visuals": [_primitive({"type": "capsule", "radius": 0.06, "halfHeight": 0.24}, (0, 0, 0.3))],
            },
            {
                "kind": "body",
                "entityKey": LINK2,
                "name": "Link2",
                "parentEntityKey": BASE + "/Robot",
                "streamId": 2,
                "visuals": [
                    _primitive({"type": "capsule", "radius": 0.045, "halfHeight": 0.2}, (0, 0, 0.25))
                ],
            },
            {
                "kind": "body",
                "entityKey": BASE + "/Centrifuge/Rotor",
                "name": "Rotor",
                "parentEntityKey": BASE + "/Centrifuge",
                "streamId": 30,
                "visuals": [_primitive({"type": "box", "halfExtents": [0.22, 0.04, 0.025]})],
            },
            {
                "kind": "body",
                "entityKey": BASE + "/FallbackBox",
                "name": "Missing asset fallback",
                "parentEntityKey": BASE,
                "streamId": 31,
                "visuals": [
                    {
                        "kind": "glb",
                        "url": "/api/v1/assets/missing-reference.glb",
                        "assetUpAxis": "Y",
                        "scale": [1, 1, 1],
                        "localPose": IDENTITY,
                    }
                ],
                "colliders": [_primitive({"type": "box", "halfExtents": [0.1, 0.1, 0.1]})],
            },
        ]
        for index in range(20):
            nodes.append(
                {
                    "kind": "body",
                    "entityKey": BASE + f"/Tubes/Tube{index + 1:02d}",
                    "name": f"Tube {index + 1:02d}",
                    "parentEntityKey": BASE + "/Tubes",
                    "streamId": 10 + index,
                    "visuals": [_primitive({"type": "cylinder", "radius": 0.025, "halfHeight": 0.1})],
                }
            )
        return SceneManifest.model_validate(
            {
                "sessionId": self.session_id,
                "sceneRevision": "rev-" + uuid.uuid4().hex[:12],
                "revisionNumericId": 1,
                "epoch": 0,
                "sceneName": "Synthetic Laboratory",
                "source": {
                    "kind": "synthetic",
                    "solver": "DeterministicKinematics",
                    "physicsDt": self.options.physicsDt,
                    "basePublishHz": self.options.basePublishHz,
                    "realtimeFactor": self.options.realtimeFactor,
                },
                "nodes": nodes,
                "sites": [
                    {
                        "entityKey": JOINT1 + "/anchor",
                        "role": "joint-anchor",
                        "parentBodyKey": LINK1,
                        "jointKey": JOINT1,
                        "localPose": IDENTITY,
                    },
                    {
                        "entityKey": JOINT2 + "/anchor",
                        "role": "joint-anchor",
                        "parentBodyKey": LINK2,
                        "jointKey": JOINT2,
                        "localPose": IDENTITY,
                    },
                    {
                        "entityKey": BASE + "/Robot/tcp",
                        "role": "generic",
                        "parentBodyKey": LINK2,
                        "localPose": {"p": [0, 0, 0.5], "q": [0, 0, 0, 1]},
                    },
                ],
                "joints": [
                    {
                        "entityKey": JOINT1,
                        "type": "revolute",
                        "parentBodyKey": None,
                        "childBodyKey": LINK1,
                        "parentLocalPose": {"p": [0, 0, 0.65], "q": [0, 0, 0, 1]},
                        "axis": [0, 1, 0],
                        "anchorSiteKey": JOINT1 + "/anchor",
                        "dofCount": 1,
                    },
                    {
                        "entityKey": JOINT2,
                        "type": "revolute",
                        "parentBodyKey": LINK1,
                        "childBodyKey": LINK2,
                        "parentLocalPose": {"p": [0, 0, 0.6], "q": [0, 0, 0, 1]},
                        "axis": [0, 1, 0],
                        "anchorSiteKey": JOINT2 + "/anchor",
                        "dofCount": 1,
                    },
                ],
                "warnings": [
                    {
                        "code": "asset_missing",
                        "entityKey": BASE + "/FallbackBox",
                        "message": "Intentional missing GLB exercises collider fallback",
                    },
                    {
                        "code": "asset_fallback",
                        "entityKey": BASE + "/FallbackBox",
                        "message": "Use primitive collider when the GLB is unavailable",
                    },
                ],
            }
        )

    async def execute(self, command: Command) -> CommandResult:
        async with self._lock:
            try:
                args = validate_command_args(command)
            except ValueError as exc:
                return self._rejected(
                    command, "unknown_command" if str(exc) == "unknown_command" else "invalid_args"
                )
            if self._state not in ("RUNNING", "PAUSED"):
                return self._rejected(command, "source_unavailable")
            kind = command.command
            if (
                (kind == "step" and self._state != "PAUSED")
                or (kind == "pause" and self._state != "RUNNING")
                or (kind == "resume" and self._state != "PAUSED")
            ):
                return self._rejected(command, "invalid_state")
            frame = None
            if kind == "pause":
                self._state = "PAUSED"
                self._actual_rtf = 0
                # Flush the final authoritative sample before PAUSED so every observer settles.
                frame = self._publish()
            elif kind == "resume":
                self._state = "RUNNING"
                frame = self._publish()
            elif kind == "reset":
                self._step_index = 0
                self._epoch += 1
                frame = self._publish("stream.snapshot")
            elif kind == "step":
                self._step_index += args.count
                frame = self._publish()
            elif kind == "set_realtime_factor":
                self.options.realtimeFactor = args.value
            self._last_clock = time.monotonic()
            self._accumulator = 0
            ack = CommandAck(
                requestId=command.requestId, status="applied", simTime=self.sim_time, epoch=self._epoch
            )
            state = SessionState(
                sessionId=self.session_id,
                state=self._state,
                sceneRevision=self.manifest.sceneRevision,
                epoch=self._epoch,
                seq=self._seq,
                simTime=self.sim_time,
                realtimeFactor=self.options.realtimeFactor,
                actualRtf=0 if self._state == "PAUSED" else self._actual_rtf,
                cause={"kind": "command", "requestId": command.requestId, "command": kind},
            )
            return CommandResult(ack=ack, frame=frame, state=state)

    def _rejected(self, command, reason):
        return CommandResult(
            ack=CommandAck(
                requestId=command.requestId,
                status="rejected",
                reason=reason,
                simTime=self.sim_time,
                epoch=self._epoch,
            )
        )

    async def query_joint_state(self, joint_key: str) -> JointState:
        if joint_key not in (JOINT1, JOINT2):
            raise KeyError(joint_key)
        t = self.sim_time
        return JointState(
            entityKey=joint_key,
            simTime=t,
            epoch=self._epoch,
            positions=[0.45 * math.sin(t) if joint_key == JOINT1 else 0.6 * math.sin(t * 0.7 + 0.5)],
        )

    async def read_mesh(self, mesh_id: str) -> bytes:
        raise KeyError(mesh_id)

    async def close(self):
        self._state = "STOPPED"
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._events.put_nowait(None)
