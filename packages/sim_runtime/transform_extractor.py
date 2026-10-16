"""Step-boundary staging; a readback thread waits for CUDA, never the solver thread."""

import queue
import threading
from concurrent.futures import Future
from time import perf_counter

import numpy as np
import warp as wp

from .pose_filter import PoseFilter


class TransformExtractor:
    def __init__(self, body_count: int, *, device):
        self.device = wp.get_device(device)
        self.body_count = body_count
        self.filter = PoseFilter(body_count)
        self.readback_mode = "cuda-staged-pinned-double-buffer" if self.device.is_cuda else "cpu-bulk"
        self.readback_ms = 0.0
        self.readback_latency_ms = 0.0
        self.enqueue_ms = 0.0
        self.host_copy_ms = 0.0
        self.filter_ms = 0.0
        self.last_metrics = {}
        self.dropped_captures = 0
        self.error = None
        self._closed = False
        self._free = queue.Queue()
        self._jobs = queue.Queue()
        self._slots = []
        if self.device.is_cuda:
            self.sim_stream = wp.get_stream(self.device)
            self.copy_stream = wp.Stream(self.device)
        for index in range(2):
            slot = {"host": np.empty((body_count, 7), dtype=np.float32)}
            if self.device.is_cuda:
                slot = {
                    "staging": wp.empty(body_count, dtype=wp.transform, device=self.device),
                    "host": wp.empty(body_count, dtype=wp.transform, device="cpu", pinned=True),
                    "ready": wp.Event(self.device),
                    "copy_start": wp.Event(self.device, enable_timing=True),
                    "done": wp.Event(self.device, enable_timing=True),
                }
            self._slots.append(slot)
            self._free.put(index)
        self.thread = threading.Thread(target=self._run, name="newton-readback", daemon=True)
        self.thread.start()

    def capture(self, body_q, *, on_complete=None, block=False) -> Future | None:
        if self._closed or self.error:
            raise RuntimeError(f"Transform extractor unavailable: {self.error or 'closed'}")
        if len(body_q) != self.body_count:
            raise ValueError("Unexpected model body transform count")
        try:
            index = self._free.get(timeout=5) if block else self._free.get_nowait()
        except queue.Empty:
            if block:
                raise TimeoutError("No free transform readback buffer") from None
            self.dropped_captures += 1
            return None
        future = Future()
        slot = self._slots[index]
        started = perf_counter()
        cpu_copy_ms = 0.0
        cuda_copy = self.device.is_cuda and not isinstance(body_q, np.ndarray)
        try:
            if cuda_copy:
                # Each slot owns its staging until the copy and publication finish.
                # The simulation stream orders this copy before the next state write.
                with wp.ScopedStream(self.sim_stream, sync_enter=False, sync_exit=False):
                    wp.copy(slot["staging"], body_q)
                    wp.record_event(slot["ready"])
                with wp.ScopedStream(self.copy_stream, sync_enter=False, sync_exit=False):
                    wp.wait_event(slot["ready"])
                    wp.record_event(slot["copy_start"])
                    wp.copy(slot["host"], slot["staging"])
                    wp.record_event(slot["done"])
            else:
                target = slot["host"].numpy() if self.device.is_cuda else slot["host"]
                np.copyto(target, body_q if isinstance(body_q, np.ndarray) else body_q.numpy())
                cpu_copy_ms = (perf_counter() - started) * 1000
            enqueue_ms = (perf_counter() - started) * 1000
            self._jobs.put((index, future, on_complete, started, enqueue_ms, cpu_copy_ms, cuda_copy))
        except BaseException:
            self._free.put(index)
            raise
        return future

    def _run(self):
        while (job := self._jobs.get()) is not None:
            index, future, callback, started, enqueue_ms, cpu_copy_ms, cuda_copy = job
            slot = self._slots[index]
            try:
                if cuda_copy:
                    wp.synchronize_event(slot["done"])
                    copied = perf_counter()
                    sample = slot["host"].numpy().copy()
                    self.host_copy_ms = (perf_counter() - copied) * 1000
                    self.readback_ms = wp.get_event_elapsed_time(
                        slot["copy_start"], slot["done"], synchronize=False
                    )
                else:
                    copied = perf_counter()
                    sample = (slot["host"].numpy() if self.device.is_cuda else slot["host"]).copy()
                    self.host_copy_ms = (perf_counter() - copied) * 1000
                    self.readback_ms = cpu_copy_ms
                self.enqueue_ms = enqueue_ms
                self.readback_latency_ms = (perf_counter() - started) * 1000
                filtered = perf_counter()
                poses = self.filter.update(sample)
                self.filter_ms = (perf_counter() - filtered) * 1000
                self.last_metrics = {
                    "readbackMs": self.readback_ms,
                    "enqueueMs": self.enqueue_ms,
                    "hostCopyMs": self.host_copy_ms,
                    "filterMs": self.filter_ms,
                    "latencyMs": self.readback_latency_ms,
                    "readbackBodies": self.body_count,
                    "readbackBytes": self.body_count * 28,
                }
                future.set_result(poses)
                if callback:
                    callback(poses)
            except BaseException as exc:
                self.error = str(exc)
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._free.put(index)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._jobs.put(None)
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise TimeoutError("Transform readback thread did not stop")
