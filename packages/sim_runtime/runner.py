"""Spawn entrypoint. Importing this module does not import the simulation engine."""

import os
import time
import uuid

from packages.protocol.internal import CommandResult, JointState, SessionOptions
from packages.protocol.schema import (
    Command,
    CommandAck,
    ServerError,
    SessionState,
    TransformFrame,
    validate_command_args,
)
from packages.transport import FrameCodec, PipeWriter


def _body_targets(manifest):
    """Manifest identity owns the mapping; compact gather order is not a stream ID."""
    bodies = sorted((node for node in manifest.nodes if node.kind == "body"), key=lambda node: node.entityKey)
    return [node.debug["newtonBodyIndex"] for node in bodies], [node.streamId for node in bodies]


class _Runner:
    def __init__(self, transport, session_id, scene_id, options):
        # Only the spawned process reaches these imports or owns a Newton State.
        import newton
        import warp as wp

        from .manifest_export import export_manifest
        from .pacer import Pacer
        from .simulation import NewtonSimulation
        from .transform_extractor import TransformExtractor

        self.wp, self.newton = wp, newton
        self.transport, self.session_id, self.options = transport, session_id, options
        self.sim = NewtonSimulation(scene_id, device=options.device, physics_dt=options.physicsDt)
        self.manifest = export_manifest(
            self.sim,
            session_id=session_id,
            scene_revision="rev-" + uuid.uuid4().hex,
            revision_numeric_id=1,
            publish_hz=options.basePublishHz,
            realtime_factor=options.realtimeFactor,
            strict_labels=options.strictLabels,
        )
        body_indices, self.stream_ids = _body_targets(self.manifest)
        self.body_indices = body_indices
        self._body_by_stream = dict(zip(self.stream_ids, body_indices, strict=True))
        self._interest = None
        self._last_complete = None
        self.extractor = TransformExtractor(
            self.sim.model.body_count, device=self.sim.model.device, body_indices=body_indices
        )
        self.codec = FrameCodec(session_id, self.manifest.sceneRevision, 1)
        self.frames = PipeWriter(transport.frames, latest_only=True, name="newton-frame-sender")
        self.control = PipeWriter(transport.control, name="newton-control-sender")
        self.pacer = Pacer(
            physics_dt=options.physicsDt,
            realtime_factor=options.realtimeFactor,
            max_lag=options.maxLagMs / 1000,
        )
        self.seq = 0
        self.actual_rtf = 0.0
        self.step_hz = 0.0
        self.publish_ms = 0.0
        self._metric_clock, self._metric_steps, self._steps = time.monotonic(), 0, 0
        self._joint_q = wp.empty_like(self.sim.state.joint_q)
        self._joint_qd = wp.empty_like(self.sim.state.joint_qd)
        # Maximal-coordinate solvers need IK for their on-demand joint query.
        # Reduced-coordinate solvers already maintain authoritative joint_q.
        if self.sim.solver_kind == "xpbd":
            newton.eval_ik(self.sim.model, self.sim.state, self._joint_q, self._joint_qd)
        # Compile the real contact/solver path while LOADING, then restore the
        # zero-step FK authority before advertising readiness or accepting commands.
        self.sim.step(self.sim.config.warmup_steps)
        self.sim.reset(increment_epoch=False)
        self.sim.solver_ms = 0.0
        wp.synchronize_device(self.sim.model.device)
        self._joint_starts = self.sim.model.joint_q_start.numpy()
        self._joint_keys = {joint.entityKey: index for index, joint in enumerate(self.manifest.joints)}
        self._heartbeat_seq = 1
        self._heartbeat_at = time.monotonic()

    def stats(self):
        return {
            "runnerPID": os.getpid(),
            "solver_ms": self.sim.solver_ms,
            "solverTimingMode": "host-submission" if self.sim.model.device.is_cuda else "cpu-wall",
            "readback_ms": self.extractor.readback_ms,
            "readbackLatencyMs": self.extractor.readback_latency_ms,
            "gather_ms": self.extractor.gather_ms,
            "gatherEnqueueMs": self.extractor.enqueue_ms,
            "hostCopyMs": self.extractor.host_copy_ms,
            "poseFilterMs": self.extractor.filter_ms,
            "gatherBodyCount": len(self.stream_ids) if self._interest is None else len(self._interest),
            "readbackBodyCount": self.extractor.last_metrics.get("readbackBodies", 0),
            "readbackBytes": self.extractor.last_metrics.get("readbackBytes", 0),
            "modelBodyCount": self.sim.model.body_count,
            "shapeCount": int(self.sim.model.shape_count),
            "gatherMode": "subscription-union-periodic-full-control",
            "runnerHeartbeatSeq": self._heartbeat_seq,
            "runnerHeartbeatMs": (time.monotonic() - self._heartbeat_at) * 1000,
            "publish_ms": self.publish_ms,
            "readbackMode": self.extractor.readback_mode,
            "stepHz": self.step_hz,
            "actualRtf": self.actual_rtf if not self.sim.paused else 0.0,
            "invalidCounts": {
                "total": self.extractor.filter.total_invalid,
                "consecutiveByBody": self.extractor.filter.consecutive_invalid.tolist(),
                "newtonBodyIndices": list(range(self.sim.model.body_count)),
            },
            "droppedPeriodicFrames": self.frames.slot.overwritten + self.extractor.dropped_captures,
            "droppedCatchupSeconds": self.pacer.dropped_wall_seconds,
        }

    def _frame(self, kind="transform.frame", *, reliable=False):
        stream_ids = (
            self.stream_ids
            if reliable or self._interest is None
            else [value for value in self.stream_ids if value in self._interest]
        )
        body_indices = [self._body_by_stream[value] for value in stream_ids]
        self.seq += 1
        metadata = {
            "type": kind,
            "sessionId": self.session_id,
            "sceneRevision": self.manifest.sceneRevision,
            "epoch": self.sim.epoch,
            "seq": self.seq,
            "simTime": self.sim.sim_time,
            "sampleTimeMs": time.monotonic() * 1000,
        }

        def make_frame(poses):
            return TransformFrame(
                **metadata,
                poses=[(stream_id, *row) for stream_id, row in zip(stream_ids, poses.tolist(), strict=True)],
            )

        def publish(poses):
            started = time.perf_counter()
            self.frames.offer(self.codec.encode(make_frame(poses)))
            self.publish_ms = (time.perf_counter() - started) * 1000
            self._warn_invalid_poses()

        future = self.extractor.capture(
            self.sim.state.body_q,
            body_indices=body_indices,
            on_complete=None if reliable else publish,
            block=reliable,
        )
        if reliable:
            frame = make_frame(future.result(timeout=5))
            self._last_complete = frame
            self._warn_invalid_poses()
            return frame
        return None

    def _warn_invalid_poses(self):
        indices = self.extractor.filter.warning_indices
        if indices:
            self.control.offer(
                {
                    "kind": "event",
                    "event": ServerError(
                        code="internal_error",
                        message=f"Invalid Newton poses for 30 consecutive frames: {indices}",
                    ).model_dump(),
                }
            )

    def _state(self, cause):
        return SessionState(
            sessionId=self.session_id,
            state="PAUSED" if self.sim.paused else "RUNNING",
            sceneRevision=self.manifest.sceneRevision,
            epoch=self.sim.epoch,
            seq=self.seq,
            simTime=self.sim.sim_time,
            realtimeFactor=self.options.realtimeFactor,
            actualRtf=self.actual_rtf if not self.sim.paused else 0,
            cause=cause,
        )

    def _execute(self, command):
        reason = None
        try:
            args = validate_command_args(command)
        except ValueError as exc:
            reason = "unknown_command" if str(exc) == "unknown_command" else "invalid_args"
        if reason is None and (
            (command.command == "step" and not self.sim.paused)
            or (command.command == "pause" and self.sim.paused)
            or (command.command == "resume" and not self.sim.paused)
        ):
            reason = "invalid_state"
        if reason:
            return CommandResult(
                ack=CommandAck(
                    requestId=command.requestId,
                    status="rejected",
                    reason=reason,
                    simTime=self.sim.sim_time,
                    epoch=self.sim.epoch,
                )
            )
        frame = None
        kind = command.command
        if kind == "pause":
            self.sim.pause()
            frame = self._frame(reliable=True)
        elif kind == "resume":
            self.sim.resume()
            frame = self._frame(reliable=True)
        elif kind == "reset":
            self.sim.reset()
            frame = self._frame("stream.snapshot", reliable=True)
        elif kind == "step":
            self.sim.step(args.count)
            self._steps += args.count
            frame = self._frame(reliable=True)
        elif kind == "set_realtime_factor":
            self.options.realtimeFactor = args.value
            self.pacer.realtime_factor = args.value
        self.pacer.reset_clock()
        self._metric_clock, self._metric_steps = time.monotonic(), self._steps
        self.actual_rtf = 0.0
        return CommandResult(
            ack=CommandAck(
                requestId=command.requestId, status="applied", simTime=self.sim.sim_time, epoch=self.sim.epoch
            ),
            frame=frame,
            state=self._state({"kind": "command", "requestId": command.requestId, "command": kind}),
        )

    def _handle(self, packet):
        kind, token = packet["kind"], packet["token"]
        if kind == "close":
            self.control.offer({"kind": "closed", "token": token})
            return False
        if kind in ("interest", "snapshot"):
            if kind == "interest":
                values = packet["streamIds"]
                if values is not None and (
                    any(type(value) is not int for value in values)
                    or len(values) != len(set(values))
                    or not set(values) <= self._body_by_stream.keys()
                ):
                    raise ValueError("Unknown or invalid interest stream IDs")
                self._interest = None if values is None else frozenset(values)
            frame = self._last_complete if self.sim.paused else None
            if frame is None:
                frame = self._frame("stream.snapshot", reliable=True)
            self.control.offer(
                {
                    "kind": kind + "_result",
                    "token": token,
                    "frame": frame.model_copy(update={"type": "stream.snapshot"}).model_dump(),
                    "streamIds": None if self._interest is None else sorted(self._interest),
                    "stats": self.stats(),
                }
            )
            return True
        if kind == "command":
            result = self._execute(Command.model_validate(packet["command"]))
            self.control.offer(
                {
                    "kind": "result",
                    "token": token,
                    "result": result.model_dump(),
                    "stats": self.stats(),
                }
            )
        elif kind == "joint":
            key = packet["key"]
            if key not in self._joint_keys:
                self.control.offer({"kind": "unknown_entity", "token": token, "key": key})
            else:
                index = self._joint_keys[key]
                coordinates = self.sim.state.joint_q
                if self.sim.solver_kind == "xpbd":
                    self.newton.eval_ik(self.sim.model, self.sim.state, self._joint_q, self._joint_qd)
                    coordinates = self._joint_q
                positions = coordinates.numpy()[self._joint_starts[index] : self._joint_starts[index + 1]]
                state = JointState(
                    entityKey=key,
                    epoch=self.sim.epoch,
                    simTime=self.sim.sim_time,
                    positions=positions.tolist(),
                )
                self.control.offer({"kind": "joint_result", "token": token, "state": state.model_dump()})
        elif kind == "mesh":
            try:
                data = self.sim.meshes.read(packet["key"])
            except KeyError:
                self.control.offer({"kind": "unknown_entity", "token": token, "key": packet["key"]})
            else:
                self.control.offer({"kind": "mesh_result", "token": token, "data": data})
        else:
            raise ValueError(f"Unknown runner control operation: {kind}")
        return True

    def run(self):
        snapshot = self._frame("stream.snapshot", reliable=True)
        self.control.offer(
            {
                "kind": "ready",
                "manifest": self.manifest.model_dump(),
                "snapshot": snapshot.model_dump(),
                "stats": self.stats(),
            }
        )
        self.pacer.reset_clock()
        self._metric_clock, self._metric_steps = time.monotonic(), self._steps
        next_publish = time.monotonic() + 1 / self.options.basePublishHz
        next_stats = time.monotonic() + 1
        while True:
            if self.control.stopped.is_set() or self.frames.stopped.is_set() or self.extractor.error:
                raise RuntimeError(self.extractor.error or "Runner pipe disconnected")
            if self.transport.control.poll():
                if not self._handle(self.transport.control.recv()):
                    break
                continue
            now = time.monotonic()
            if not self.sim.paused:
                count = self.pacer.steps_due(now)
                if count:
                    self.sim.step(count)
                    self._steps += count
                if now >= next_publish:
                    self._frame()
                    next_publish = now + 1 / self.options.basePublishHz
            else:
                count = 0
                self.pacer.reset_clock(now)
            if now >= next_stats:
                self._heartbeat_seq += 1
                self._heartbeat_at = now
                elapsed = now - self._metric_clock
                self.step_hz = (self._steps - self._metric_steps) / elapsed if elapsed else 0.0
                self.actual_rtf = self.step_hz * self.sim.physics_dt
                self._metric_clock, self._metric_steps = now, self._steps
                self.control.offer({"kind": "stats", "stats": self.stats()})
                next_stats = now + 1
            if not count:
                time.sleep(min(self.sim.physics_dt / 2, 0.002))

    def close(self):
        try:
            self.extractor.close()
            self.sim.close()
        finally:
            self.frames.close()
            self.control.close(drain=True)


def run_runner(transport, session_id: str, scene_id: str, options: dict):
    runner = None
    try:
        runner = _Runner(transport, session_id, scene_id, SessionOptions.model_validate(options))
        runner.run()
    except BaseException as exc:
        # One reliable sender owns the pipe once constructed.
        packet = {"kind": "failed", "message": f"{type(exc).__name__}: {exc}"}
        try:
            if runner:
                runner.control.offer(packet)
                runner.control.close(drain=True)
            else:
                transport.control.send(packet)
        except (OSError, EOFError, RuntimeError):
            pass
    finally:
        if runner:
            runner.close()
        transport.close()
