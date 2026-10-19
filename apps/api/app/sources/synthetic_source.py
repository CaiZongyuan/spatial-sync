"""Deterministic kinematic scene for GPU-free gateway, viewer and CI work."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
import uuid
from collections.abc import AsyncIterator

from packages.info.catalog import load_catalog
from packages.info.program import HostCore
from packages.programs.centrifuge.virtual import CentrifugeProgram
from packages.programs.robot_arm.virtual import BASE_HEIGHT, RobotArmProgram, kinematics
from packages.programs.tube.virtual import TubeProgram
from packages.protocol.internal import CommandResult, JointState, SessionOptions
from packages.protocol.math import compose_pose
from packages.protocol.schema import (
    Command,
    CommandAck,
    SceneManifest,
    SessionState,
    TransformFrame,
    validate_command_args,
)

IDENTITY = {"p": [0, 0, 0], "q": [0, 0, 0, 1]}
BASE = "/World/Lab"
LINK1, LINK2 = BASE + "/Robot/Link1", BASE + "/Robot/Link2"
JOINT1, JOINT2 = BASE + "/Robot/Joint1", BASE + "/Robot/Joint2"
ROBOT, CENTRIFUGE = BASE + "/RobotA", BASE + "/Centrifuge"
REGISTRY = {program.id: program for program in (RobotArmProgram, TubeProgram, CentrifugeProgram)}


def _primitive(geometry, p=(0, 0, 0)):
    return {"kind": "primitive", "geometry": geometry, "localPose": {"p": p, "q": [0, 0, 0, 1]}}


class SyntheticSource:
    def __init__(self, session_id: str, *, publication_clock=None):
        self.session_id = session_id
        self.scene_package = None
        self.options = SessionOptions()
        self._task: asyncio.Task | None = None
        self._events = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._step_index = 0
        self._seq = 0
        self._epoch = 0
        self._state = "CREATED"
        self._latest: TransformFrame | None = None
        self._complete: TransformFrame | None = None
        self._last_clock = time.monotonic()
        self._accumulator = 0.0
        self._actual_rtf = 0.0
        self._control_index = 0
        self._rotor_angle = 0.0
        self._publication_clock = publication_clock

    def configure_scene(self, scene_package):
        self.scene_package = scene_package

    async def start(self, scene_id: str, options: SessionOptions):
        if scene_id != "lab-demo":
            raise ValueError("unknown synthetic scene")
        self.options = options.model_copy()
        self.manifest = self._manifest()
        self.catalog = load_catalog(
            self.manifest, self.scene_package.name if self.scene_package else scene_id, REGISTRY,
            **({"scene_file": self.scene_package.path / "devices.yaml"} if self.scene_package else {}),
        )
        self.host = HostCore(
            self.catalog, REGISTRY, fact_hz=options.factHz, publication_clock=self._publication_clock
        )
        self._centrifuge_phase = self.host.reader(CENTRIFUGE, "phase")
        self._centrifuge_rpm = self.host.reader(CENTRIFUGE, "rpm")
        self._joint_positions = self.host.reader(ROBOT, "jointPositions")
        self._lid_open = self.host.reader(CENTRIFUGE, "lidOpen")
        self._tube_locations = [
            self.host.reader(BASE + f"/Tubes/Tube{index + 1:02d}", "location") for index in range(20)
        ]
        self._state = "RUNNING"
        self._publish("stream.snapshot")
        self._last_clock = time.monotonic()
        self._task = asyncio.create_task(self._run(), name=f"synthetic-{self.session_id}")
        return self.manifest, self.catalog

    def entity_snapshot(self):
        return self.host.snapshot()

    async def invoke(self, request):
        async with self._lock:
            result = self.host.invoke(request)
            for event in self.host.drain_events():
                self._events.put_nowait(event)
            self._events.put_nowait(result)
            return result

    async def cancel(self, invocation_id):
        async with self._lock:
            result = self.host.cancel(invocation_id)
            for event in self.host.drain_events():
                self._events.put_nowait(event)
            return result

    def _advance(self, count):
        self._step_index += count
        due = int(self.sim_time * self.options.controlHz + 1e-9)
        while self._control_index < due:
            self._control_index += 1
            for event in self.host.tick(
                self._control_index / self.options.controlHz, 1 / self.options.controlHz
            ):
                self._events.put_nowait(event)
            if self._centrifuge_phase() == "idle":
                self._rotor_angle = 0.0
            else:
                self._rotor_angle += (
                    min(self._centrifuge_rpm(), 60) / 60 * 2 * math.pi / self.options.controlHz
                )

    @property
    def sim_time(self) -> float:
        return self._step_index * self.options.physicsDt

    def latest_frame(self) -> TransformFrame | None:
        """Published objects are replaced as a whole and never subsequently mutated."""
        return self._latest

    async def snapshot(self) -> TransformFrame:
        async with self._lock:
            frame = self._complete if self._state == "PAUSED" else self._publish("stream.snapshot")
            return frame.model_copy(update={"type": "stream.snapshot"})

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
                    for event in self.host.drain_events():
                        self._events.put_nowait(event)
                    self._actual_rtf = 0.0
                    continue
                rtf = self.options.realtimeFactor
                if rtf is None:
                    steps = max(1, int((1 / self.options.basePublishHz) / self.options.physicsDt))
                else:
                    self._accumulator += min(elapsed, self.options.maxLagMs / 1000) * rtf
                    steps = int(self._accumulator / self.options.physicsDt)
                    self._accumulator -= steps * self.options.physicsDt
                target_sim_time = (self._step_index + steps) * self.options.physicsDt
                will_tick = int(target_sim_time * self.options.controlHz + 1e-9) > self._control_index
                if not will_tick:
                    for event in self.host.drain_events():
                        self._events.put_nowait(event)
                self._advance(steps)
                self._actual_rtf = steps * self.options.physicsDt / elapsed if elapsed > 0 else 0.0
                if now >= next_publish:
                    self._publish()
                    period = 1 / self.options.basePublishHz
                    # Preserve the cadence across coarse timer wakeups; skip missed slots.
                    next_publish += (int((now - next_publish) / period) + 1) * period

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
        self._complete = self._latest
        return self._latest

    def _poses(self):
        first, second, hand = kinematics(self._joint_positions())
        poses = [(1, *first["p"], *first["q"]), (2, *second["p"], *second["q"]), (3, *hand["p"], *hand["q"])]
        rotor = {
            "p": [-0.5, 0.4, 0.85],
            "q": [0, 0, math.sin(self._rotor_angle / 2), math.cos(self._rotor_angle / 2)],
        }
        locations = self.catalog.entities[ROBOT]["config"]["locations"]["locations"]
        site_poses = {value["site"]: {"p": value["p"], "q": [0, 0, 0, 1]} for value in locations.values()}
        for i, local in enumerate(((0.1, 0, 0.1), (0, 0.1, 0.1), (-0.1, 0, 0.1), (0, -0.1, 0.1))):
            site_poses[CENTRIFUGE + f"/Rotor/Slot{i + 1}"] = compose_pose(
                rotor, {"p": local, "q": [0, 0, 0, 1]}
            )
        for index, read_location in enumerate(self._tube_locations):
            location = read_location()
            pose = (
                {"p": hand["p"], "q": [0, 0, 0, 1]}
                if location == "held"
                else site_poses.get(
                    location,
                    {"p": [1.05 + (index % 4) * 0.08, -0.3 + (index // 4) * 0.1, 0.82], "q": [0, 0, 0, 1]},
                )
            )
            poses.append((10 + index, *pose["p"], *pose["q"]))
        lid_angle = 1.4 if self._lid_open() else 0.0
        for invocation in self.host.active_invocations(CENTRIFUGE):
            if (
                invocation.entityKey == CENTRIFUGE
                and invocation.status == "running"
                and invocation.action in ("openLid", "closeLid")
            ):
                progress = min(1, max(0, self.sim_time - invocation.startedSimTime))
                lid_angle = 1.4 * (progress if invocation.action == "openLid" else 1 - progress)
        poses += [
            (30, *rotor["p"], *rotor["q"]),
            (31, -0.7, -0.6, 0.5, 0, 0, 0, 1),
            (32, -0.7, 0.4, 1.0, 0, math.sin(lid_angle / 2), 0, math.cos(lid_angle / 2)),
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
                "parentEntityKey": ROBOT,
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
                "initialPose": {"p": [0.4, 0, 0.375], "q": [0, 0, 0, 1]},
                "visuals": [_primitive({"type": "box", "halfExtents": [1.3, 0.8, 0.375]})],
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
                    "visuals": [
                        _primitive({"type": "cylinder", "radius": 0.008, "halfHeight": 0.04}, (0, 0, 0.04))
                    ],
                }
            )
        nodes.extend(
            [
                {"kind": "group", "entityKey": ROBOT, "name": "Robot arm", "parentEntityKey": BASE},
                {"kind": "group", "entityKey": BASE + "/Rack", "name": "Tube rack", "parentEntityKey": BASE},
                {
                    "kind": "static",
                    "entityKey": BASE + "/Rack/Base",
                    "name": "Tube rack base",
                    "parentEntityKey": BASE + "/Rack",
                    "initialPose": {"p": [0.7, -0.2, 0.78], "q": [0, 0, 0, 1]},
                    "visuals": [_primitive({"type": "box", "halfExtents": [0.25, 0.18, 0.02]})],
                },
                {
                    "kind": "static",
                    "entityKey": CENTRIFUGE + "/Housing",
                    "name": "Centrifuge housing",
                    "parentEntityKey": CENTRIFUGE,
                    "initialPose": {"p": [-0.5, 0.4, 0.8], "q": [0, 0, 0, 1]},
                    "visuals": [_primitive({"type": "box", "halfExtents": [0.22, 0.22, 0.05]})],
                },
                {
                    "kind": "body",
                    "entityKey": ROBOT + "/Hand",
                    "name": "Hand",
                    "parentEntityKey": ROBOT,
                    "streamId": 3,
                    "visuals": [_primitive({"type": "box", "halfExtents": [0.04, 0.04, 0.025]})],
                },
                {
                    "kind": "body",
                    "entityKey": CENTRIFUGE + "/Lid",
                    "name": "Lid",
                    "parentEntityKey": CENTRIFUGE,
                    "streamId": 32,
                    "visuals": [_primitive({"type": "box", "halfExtents": [0.2, 0.2, 0.015]}, (0.2, 0, 0))],
                },
            ]
        )
        import yaml

        from packages.library.scenes import builtin_scene_path

        locations = yaml.safe_load(
            ((self.scene_package.path if self.scene_package else builtin_scene_path("lab-demo"))
             / "locations.yaml").read_text(encoding="utf-8")
        )["locations"]
        sites = []
        for name, location in locations.items():
            if name.startswith("centrifuge"):
                index = int(name[-1]) - 1
                local = ((0.1, 0, 0.1), (0, 0.1, 0.1), (-0.1, 0, 0.1), (0, -0.1, 0.1))[index]
                sites.append(
                    {
                        "entityKey": location["site"],
                        "parentBodyKey": CENTRIFUGE + "/Rotor",
                        "role": "generic",
                        "localPose": {"p": local, "q": [0, 0, 0, 1]},
                    }
                )
            else:
                sites.append(
                    {
                        "entityKey": location["site"],
                        "role": "generic",
                        "localPose": {"p": location["p"], "q": [0, 0, 0, 1]},
                    }
                )
        return SceneManifest.model_validate(
            {
                "sessionId": self.session_id,
                "sceneRevision": "rev-" + uuid.uuid4().hex[:12],
                "revisionNumericId": 1,
                "epoch": 0,
                "sceneName": (
                    self.scene_package.metadata["title"] if self.scene_package else "Synthetic Laboratory"
                ),
                "source": {
                    "kind": "synthetic",
                    "solver": "DeterministicKinematics",
                    "physicsDt": self.options.physicsDt,
                    "basePublishHz": self.options.basePublishHz,
                    "realtimeFactor": self.options.realtimeFactor,
                },
                "nodes": nodes,
                "sites": sites
                + [
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
                        "parentBodyKey": ROBOT + "/Hand",
                        "localPose": IDENTITY,
                    },
                    {
                        "entityKey": ROBOT + "/tcp",
                        "role": "generic",
                        "parentBodyKey": ROBOT + "/Hand",
                        "localPose": IDENTITY,
                    },
                ],
                "joints": [
                    {
                        "entityKey": JOINT1,
                        "type": "revolute",
                        "parentBodyKey": None,
                        "childBodyKey": LINK1,
                        "parentLocalPose": {"p": [0, 0, BASE_HEIGHT], "q": [0, 0, 0, 1]},
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
                self._control_index = 0
                self._rotor_angle = 0.0
                self._epoch += 1
                for event in self.host.reset(self._epoch):
                    self._events.put_nowait(event)
                frame = self._publish("stream.snapshot")
            elif kind == "step":
                self._advance(args.count)
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
        joints = self._joint_positions()
        return JointState(
            entityKey=joint_key,
            simTime=self.sim_time,
            epoch=self._epoch,
            positions=[joints[1] if joint_key == JOINT1 else joints[2]],
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
