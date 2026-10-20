"""Deterministic kinematic scene for GPU-free gateway, viewer and CI work."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import AsyncIterator

from packages.info.catalog import load_catalog
from packages.info.program import HostCore
from packages.programs.biosafety_cabinet.virtual import BiosafetyCabinetProgram
from packages.programs.centrifuge.virtual import CentrifugeProgram
from packages.programs.env_sensor.virtual import EnvSensorProgram
from packages.programs.incubator.virtual import IncubatorProgram
from packages.programs.robot_arm.virtual import RobotArmProgram, kinematics
from packages.programs.tube.virtual import TubeProgram
from packages.protocol.internal import CommandResult, JointState, SessionOptions
from packages.protocol.math import compose_pose
from packages.protocol.schema import (
    Command,
    CommandAck,
    SessionState,
    TransformFrame,
    validate_command_args,
)
from packages.sim_runtime.synthetic_lab import (
    BASE,
    CENTRIFUGE,
    JOINT1,
    JOINT2,
    ROBOT,
    build_manifest,
)

REGISTRY = {
    program.id: program
    for program in (
        RobotArmProgram,
        TubeProgram,
        CentrifugeProgram,
        IncubatorProgram,
        BiosafetyCabinetProgram,
        EnvSensorProgram,
    )
}


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
            self.manifest,
            self.scene_package.name if self.scene_package else scene_id,
            REGISTRY,
            **({"scene_file": self.scene_package.path / "devices.yaml"} if self.scene_package else {}),
            template_assets=self.scene_package.template_assets if self.scene_package else None,
        )
        self.host = HostCore(
            self.catalog,
            REGISTRY,
            fact_hz=options.factHz,
            publication_clock=self._publication_clock,
            seed=options.seed,
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

    async def revoke_before_start(self, invocation_id):
        async with self._lock:
            result = self.host.revoke_before_start(invocation_id)
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
        declarations = (
            self.scene_package.declarations("synthetic").bootstrap() if self.scene_package else None
        )
        return build_manifest(self.session_id, self.options, self.scene_package, declarations=declarations)

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
