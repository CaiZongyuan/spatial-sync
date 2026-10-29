"""Spawn entrypoint. Importing this module does not import the simulation engine."""

import contextlib
import json
import multiprocessing as mp
import os
import time
import uuid
from pathlib import Path

from packages.info.preview import PreviewRequest, SourcePreview
from packages.protocol.internal import CommandResult, JointState, SessionOptions
from packages.protocol.schema import (
    Command,
    CommandAck,
    InvokeRequest,
    ServerError,
    SessionState,
    TransformFrame,
    validate_command_args,
)
from packages.transport import FrameCodec, PipeWriter


class RpcFence:
    """Bounded new-invoke IPC leases, revocable without waiting for slow physics.

    Only the gateway reserves/retires leases. The Runner claims a live lease
    immediately before the synchronous Host callback, never on its reader thread.
    Generations prevent a delayed packet from claiming a reused slot.
    """

    def __init__(self, capacity=256):
        self.slots = mp.get_context("spawn").Array("q", capacity)
        self.generation = 0

    def reserve(self):
        with self.slots.get_lock():
            for index, value in enumerate(self.slots):
                if value == 0:
                    self.generation += 1
                    self.slots[index] = self.generation
                    return index, self.generation
        raise RuntimeError("Runner admission capacity is exhausted")

    def retire(self, lease):
        index, generation = lease
        with self.slots.get_lock():
            if abs(self.slots[index]) == generation:
                self.slots[index] = 0

    def claim(self, lease):
        index, generation = lease
        with self.slots.get_lock():
            if self.slots[index] != generation:
                return False
            self.slots[index] = -generation
            return True


def _body_targets(manifest):
    """Map complete model snapshots to stable Manifest identities."""
    bodies = sorted((node for node in manifest.nodes if node.kind == "body"), key=lambda node: node.entityKey)
    return [node.debug["newtonBodyIndex"] for node in bodies], [node.streamId for node in bodies]


class _Runner:
    def __init__(self, transport, session_id, scene_id, options, scene_package=None, policy_subject=None,
                 *, simulation_subject=None, deployment_permit=None, state_root=None, rpc_fence=None):
        # Only the spawned process reaches these imports or owns a Newton State.
        import newton
        import warp as wp

        from .manifest_export import export_manifest
        from .pacer import Pacer
        from .simulation import NewtonSimulation
        from .transform_extractor import TransformExtractor

        self.wp, self.newton = wp, newton
        self.transport, self.session_id, self.options = transport, session_id, options
        self.rpc_fence = rpc_fence
        self._deferred_control = None
        simulation = scene_package.metadata.get("simulation", {}) if scene_package else {}
        config = None
        if scene_package and not scene_package.metadata["stage"].startswith("python:"):
            from .library_scene import prepare_scene_config

            config = prepare_scene_config(scene_package.runtime_payload(), include_visuals=True)
        self.sim = NewtonSimulation(
            scene_id, device=options.device, physics_dt=options.physicsDt,
            solver=simulation.get("solver"), config=config,
        )
        self.manifest = export_manifest(
            self.sim,
            session_id=session_id,
            scene_revision="rev-" + uuid.uuid4().hex,
            revision_numeric_id=1,
            publish_hz=options.basePublishHz,
            realtime_factor=options.realtimeFactor,
            strict_labels=options.strictLabels,
        )
        if scene_package:
            self.manifest = self.manifest.model_copy(update={"sceneName": scene_package.metadata["title"]})
        self.body_indices, self.stream_ids = _body_targets(self.manifest)
        self._last_complete = None
        self.extractor = TransformExtractor(self.sim.model.body_count, device=self.sim.model.device)
        self.codec = FrameCodec(session_id, self.manifest.sceneRevision, 1)
        self.frames = PipeWriter(transport.frames, latest_only=True, name="newton-frame-sender")
        self.control = PipeWriter(transport.control, name="newton-control-sender")
        self.pacer = Pacer(
            physics_dt=options.physicsDt,
            realtime_factor=options.realtimeFactor,
            max_lag=options.maxLagMs / 1000,
            # Yield to the control pipe after one 60 Hz control interval at
            # the default 240 Hz step. A 64-step catch-up can otherwise exceed
            # the 0.5 s action-intake deadline on a complex CPU scene.
            max_batch=4,
        )
        self.seq = 0
        self.actual_rtf = 0.0
        self.step_hz = 0.0
        self.publish_ms = 0.0
        self._metric_clock, self._metric_steps, self._steps = time.monotonic(), 0, 0
        self._joint_q = (wp.empty_like(self.sim.state.joint_q) if self.sim.model.joint_coord_count
                         else wp.empty(0, dtype=float, device=self.sim.model.device))
        self._joint_qd = (wp.empty_like(self.sim.state.joint_qd) if self.sim.model.joint_dof_count
                          else wp.empty(0, dtype=float, device=self.sim.model.device))
        # Maximal-coordinate solvers need IK for their on-demand joint query.
        # Reduced-coordinate solvers already maintain authoritative joint_q.
        if self.sim.solver_kind == "xpbd" and self.sim.model.joint_count:
            newton.eval_ik(self.sim.model, self.sim.state, self._joint_q, self._joint_qd)
        # Compile the real contact/solver path while LOADING, then restore the
        # zero-step FK authority before advertising readiness or accepting commands.
        self.sim.step(simulation.get("warmupSteps", self.sim.config.warmup_steps))
        self.sim.reset(increment_epoch=False)
        self.sim.solver_ms = 0.0
        wp.synchronize_device(self.sim.model.device)
        self._joint_starts = self.sim.model.joint_q_start.numpy()
        self._joint_keys = {joint.entityKey: index for index, joint in enumerate(self.manifest.joints)}
        self._heartbeat_seq = 1
        self._heartbeat_at = time.monotonic()
        from .info_host import NewtonInfoHost

        self.info = NewtonInfoHost(
            self.sim, self.manifest, options, self._info_events, scene_package=scene_package,
            policy_subject=policy_subject,
            simulation_subject=simulation_subject,
            deployment_permit=deployment_permit,
            state_root=state_root,
        )
        self.viewer = None
        self._viewer_error = None
        sleep_requested = os.getenv("SPATIAL_NEWTON_CPU_SLEEPING") == "1"
        contact_requested = os.getenv("SPATIAL_NEWTON_CONTACT_COLLISION_ONLY") == "1"
        if (sleep_requested or contact_requested) and os.getenv("SPATIAL_PASSAGE_RUN_ROOT"):
            # An owned-demo receipt, not a protocol/recording-format extension.
            directory = Path(os.environ["SPATIAL_PASSAGE_RUN_ROOT"]) / "runtime" / "native-options"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"cpu-sleeping-{os.getpid()}.json").write_text(json.dumps({
                "sessionId": session_id, "runnerPID": os.getpid(), "cpuSleepingRequested": sleep_requested,
                "nativeCpuSleepingEnabled": self.sim.native_cpu_sleeping,
                "contactCollisionOnlyRequested": contact_requested,
                "contactCollisionOnlyEnabled": bool(
                    contact_requested and self.sim.solver_kind == "mujoco" and self.sim.model.device.is_cpu
                    and self.sim.model.world_count == 1 and not self.sim.solver.mj_model.nflex
                    and not self.sim.solver.mj_model.nplugin
                ),
                "device": str(self.sim.model.device), "solver": self.sim.solver_kind,
            }, indent=2), encoding="utf-8")
        if self.info.port:
            self.extractor.readback_mode = "control-snapshot-reuse"

    def _info_events(self, events):
        for event in events:
            self.control.offer({"kind": "info_event", "event": event.model_dump(mode="json")})

    def _render_viewer(self, now):
        if self.viewer is None:
            return
        try:
            self.viewer.render(now)
        except Exception as exc:
            self._fail_viewer(exc)

    def _fail_viewer(self, error):
        self._viewer_error = f"Native viewer unavailable: {error}"
        viewer, self.viewer = self.viewer, None
        if viewer is not None:
            with contextlib.suppress(Exception):
                viewer.close()

    def _viewer_stats(self):
        if self.viewer is not None:
            try:
                return self.viewer.stats()
            except Exception as exc:
                self._fail_viewer(exc)
        return {
            "status": "unavailable" if self._viewer_error else "not_started",
            "url": None,
            "clientCount": None if self._viewer_error else 0,
            "reason": self._viewer_error,
        }

    def stats(self):
        return {
            **self.info.stats(),
            "runnerPID": os.getpid(),
            "device": str(self.sim.model.device),
            "viewer": self._viewer_stats(),
            "solver_ms": self.sim.solver_ms,
            "solverTimingMode": "host-submission" if self.sim.model.device.is_cuda else "cpu-wall",
            "readback_ms": self.extractor.readback_ms,
            "readbackLatencyMs": self.extractor.readback_latency_ms,
            "readbackEnqueueMs": self.extractor.enqueue_ms,
            "hostCopyMs": self.extractor.host_copy_ms,
            "poseFilterMs": self.extractor.filter_ms,
            "readbackBodyCount": self.extractor.last_metrics.get("readbackBodies", 0),
            "readbackBytes": self.extractor.last_metrics.get("readbackBytes", 0),
            "modelBodyCount": self.sim.model.body_count,
            "shapeCount": int(self.sim.model.shape_count),
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
        port = self.info.port
        if port and reliable and port.last_time != self.sim.sim_time:
            port.capture()
        self.seq += 1
        metadata = {
            "type": kind,
            "sessionId": self.session_id,
            "sceneRevision": self.manifest.sceneRevision,
            "epoch": self.sim.epoch,
            "seq": self.seq,
            "simTime": port.last_time if port else self.sim.sim_time,
            "sampleTimeMs": port.sample_time_ms if port else time.monotonic() * 1000,
        }

        def make_frame(poses):
            return TransformFrame(
                **metadata,
                poses=[
                    (stream_id, *poses[index].tolist())
                    for stream_id, index in zip(self.stream_ids, self.body_indices, strict=True)
                ],
            )

        def publish(poses):
            started = time.perf_counter()
            self.frames.offer(self.codec.encode(make_frame(poses)))
            self.publish_ms = (time.perf_counter() - started) * 1000
            self._warn_invalid_poses()

        future = self.extractor.capture(
            port.frame_body if port else self.sim.state.body_q,
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
            for _ in range(args.count):
                self.sim.step(1)
                self._steps += 1
                self._intake_between_steps()
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
        if kind == "snapshot":
            frame = self._last_complete if self.sim.paused else None
            if frame is None:
                frame = self._frame("stream.snapshot", reliable=True)
            self.control.offer(
                {
                    "kind": "snapshot_result",
                    "token": token,
                    "frame": frame.model_copy(update={"type": "stream.snapshot"}).model_dump(),
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
                    "entitySnapshot": self.info.snapshot().model_dump(mode="json")
                    if packet["command"]["command"] == "reset"
                    else None,
                    "stats": self.stats(),
                }
            )
        elif kind in ("invoke", "cancel", "revoke_before_start"):
            def admit():
                if time.monotonic() >= packet.get("expiresAt", float("inf")):
                    return False
                lease = packet.get("admission")
                return lease is None or (self.rpc_fence is not None and self.rpc_fence.claim(lease))

            try:
                result = (
                    self.info.invoke(InvokeRequest.model_validate(packet["request"]), admit=admit)
                    if kind == "invoke"
                    # A timeout cannot withdraw an already sent safety intent
                    # against an old invocation. Only creating new work expires.
                    else getattr(self.info, kind)(packet["invocationId"])
                )
            except TimeoutError:
                self.control.offer({"kind": "rpc_expired", "token": token})
                return True
            except KeyError:
                if kind == "invoke":
                    raise
                self.control.offer({"kind": "unknown_invocation", "token": token,
                                    "invocationId": packet["invocationId"]})
                return True
            self.control.offer(
                {"kind": "invocation_result", "token": token, "update": result.model_dump(mode="json")}
            )
        elif kind == "preview":
            try:
                preview = self.info.preview(PreviewRequest.model_validate(packet["request"]))
                result = SourcePreview(sceneRevision=self.manifest.sceneRevision, epoch=self.sim.epoch,
                                       simTime=self.sim.sim_time, sampleTimeMs=time.monotonic() * 1000,
                                       preview=preview)
                self.control.offer({"kind": "preview_result", "token": token,
                                    "result": result.model_dump(mode="json")})
            except Exception as error:
                self.control.offer({"kind": "preview_error", "token": token,
                                    "invalid": isinstance(error, (ValueError, KeyError)),
                                    "message": str(error)})
        elif kind == "open_viewer":
            try:
                current = self._viewer_stats()
                if self.viewer is not None and current["status"] != "ready":
                    stale, self.viewer = self.viewer, None
                    stale.close()
                if self.viewer is None:
                    from .viewer_adapter import NativeViewer

                    self.viewer = NativeViewer(self.sim, self.session_id)
                self._viewer_error = None
                metrics = self.stats()
                if metrics["viewer"]["status"] != "ready":
                    raise RuntimeError(metrics["viewer"]["reason"] or "Native viewer did not become ready")
                self.control.offer({
                    "kind": "viewer_result", "token": token,
                    "viewer": {"status": "ready", "url": metrics["viewer"]["url"], "backendPid": os.getpid()},
                    "stats": metrics,
                })
            except Exception as exc:
                self._fail_viewer(exc)
                self.control.offer({
                    "kind": "viewer_error", "token": token, "message": self._viewer_error,
                    "stats": self.stats(),
                })
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
        elif kind == "observation_runtime":
            from packages.policies.inference import runtime_facts

            self.control.offer({"kind": "observation_runtime_result", "token": token,
                                "runtime": runtime_facts()})
        elif kind == "program_execution":
            try:
                evidence = self.info.program_execution(packet["key"])
            except KeyError:
                self.control.offer({"kind": "unknown_entity", "token": token, "key": packet["key"]})
            else:
                self.control.offer({"kind": "program_execution_result", "token": token, "evidence": evidence})
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

    def _intake_between_steps(self):
        # A manual step command still owns its exact requested step count.
        # Service intake at safe boundaries, but do not reorder it past an
        # earlier reset/resume/other control command on the reliable pipe.
        while self._deferred_control is None and self.transport.control.poll():
            packet = self.transport.control.recv()
            if packet["kind"] not in ("invoke", "cancel", "revoke_before_start"):
                self._deferred_control = packet
                break
            self._handle(packet)

    def run(self):
        snapshot = self._frame("stream.snapshot", reliable=True)
        self.control.offer(
            {
                "kind": "ready",
                "manifest": self.manifest.model_dump(),
                "snapshot": snapshot.model_dump(),
                "catalog": self.info.catalog.model_dump(mode="json"),
                "alertRules": self.info.catalog.alert_rules(),
                "entitySnapshot": self.info.snapshot().model_dump(mode="json"),
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
            packet = getattr(self, "_deferred_control", None)
            self._deferred_control = None
            if packet is None and self.transport.control.poll():
                packet = self.transport.control.recv()
            if packet is not None:
                if not self._handle(packet):
                    break
                continue
            now = time.monotonic()
            self.info.flush()
            if not self.sim.paused:
                count = self.pacer.steps_due(now)
                if count:
                    executed = 0
                    for _ in range(count):
                        if self.transport.control.poll():
                            break
                        self.sim.step(1)
                        self._steps += 1
                        executed += 1
                    self.pacer.return_steps(count - executed)
                    count = executed
                    if self.transport.control.poll():
                        continue
                if now >= next_publish:
                    self._frame()
                    next_publish = now + 1 / self.options.basePublishHz
            else:
                count = 0
                self.pacer.reset_clock(now)
            self._render_viewer(now)
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
            try:
                if getattr(self, "info", None) is not None:
                    self.info.close()
            finally:
                try:
                    if self.viewer is not None:
                        self.viewer.close()
                finally:
                    try:
                        self.extractor.close()
                    finally:
                        self.sim.close()
        finally:
            self.frames.close()
            self.control.close(drain=True)


def run_runner(transport, session_id: str, scene_id: str, options: dict,
               scene_package=None, policy_subject=None, *, simulation_subject=None,
               deployment_permit=None, state_root=None, rpc_fence=None):
    runner = None
    try:
        runner = _Runner(
            transport, session_id, scene_id, SessionOptions.model_validate(options),
            scene_package, policy_subject,
            simulation_subject=simulation_subject, deployment_permit=deployment_permit, state_root=state_root,
            rpc_fence=rpc_fence,
        )
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
