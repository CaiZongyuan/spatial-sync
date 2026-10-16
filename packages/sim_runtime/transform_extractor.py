"""Step-boundary staging; a readback thread waits for CUDA, never the solver thread."""

import queue
import threading
from concurrent.futures import Future
from operator import index as integer_index
from time import perf_counter

import numpy as np
import warp as wp

from .pose_filter import PoseFilter


@wp.kernel
def _gather_transforms(
    source: wp.array(dtype=wp.transform),
    indices: wp.array(dtype=wp.int32),
    target: wp.array(dtype=wp.transform),
):
    slot = wp.tid()
    target[slot] = source[indices[slot]]


class TransformExtractor:
    def __init__(self, body_count: int, *, device, body_indices=None):
        self.device = wp.get_device(device)
        if body_indices is None:
            indices = list(range(body_count))
        else:
            body_indices = list(body_indices)
            try:
                indices = [
                    integer_index(value) for value in body_indices if not isinstance(value, (bool, np.bool_))
                ]
            except TypeError:
                raise ValueError("Body indices must be integers") from None
            if len(indices) != len(body_indices):
                raise ValueError("Body indices must be integers")
        if len(set(indices)) != len(indices) or any(value < 0 or value >= body_count for value in indices):
            raise ValueError("Body indices must be unique and within the model")
        self.body_count = body_count
        self.body_indices = np.array(indices, dtype=np.int32)
        self.body_indices.flags.writeable = False
        self._device_indices = wp.array(self.body_indices, dtype=wp.int32, device=self.device)
        count = len(indices)
        self.filter = PoseFilter(count)
        self.readback_mode = "cuda-staged-pinned-double-buffer" if self.device.is_cuda else "cpu-bulk"
        self.readback_ms = 0.0
        self.readback_latency_ms = 0.0
        self.gather_ms = 0.0
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
            slot = {"host": np.empty((count, 7), dtype=np.float32)}
            if self.device.is_cuda:
                slot = {
                    "staging": wp.empty(count, dtype=wp.transform, device=self.device),
                    "host": wp.empty(count, dtype=wp.transform, device="cpu", pinned=True),
                    "gather_start": wp.Event(self.device, enable_timing=True),
                    "ready": wp.Event(self.device, enable_timing=True),
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
        cpu_gather_ms = 0.0
        try:
            if self.device.is_cuda:
                # Each slot owns its staging until the copy and publication finish.
                # The simulation stream orders this copy before the next state write.
                with wp.ScopedStream(self.sim_stream, sync_enter=False, sync_exit=False):
                    wp.record_event(slot["gather_start"])
                    if len(self.body_indices):
                        wp.launch(
                            _gather_transforms,
                            dim=len(self.body_indices),
                            inputs=[body_q, self._device_indices, slot["staging"]],
                            device=self.device,
                        )
                    wp.record_event(slot["ready"])
                with wp.ScopedStream(self.copy_stream, sync_enter=False, sync_exit=False):
                    wp.wait_event(slot["ready"])
                    wp.record_event(slot["copy_start"])
                    wp.copy(slot["host"], slot["staging"])
                    wp.record_event(slot["done"])
            else:
                np.take(body_q.numpy(), self.body_indices, axis=0, out=slot["host"])
                cpu_gather_ms = (perf_counter() - started) * 1000
            enqueue_ms = (perf_counter() - started) * 1000
            self._jobs.put((index, future, on_complete, started, enqueue_ms, cpu_gather_ms))
        except BaseException:
            self._free.put(index)
            raise
        return future

    def _run(self):
        while (job := self._jobs.get()) is not None:
            index, future, callback, started, enqueue_ms, cpu_gather_ms = job
            slot = self._slots[index]
            try:
                if self.device.is_cuda:
                    wp.synchronize_event(slot["done"])
                    self.gather_ms = wp.get_event_elapsed_time(
                        slot["gather_start"], slot["ready"], synchronize=False
                    )
                    copied = perf_counter()
                    sample = slot["host"].numpy().copy()
                    self.host_copy_ms = (perf_counter() - copied) * 1000
                    self.readback_ms = wp.get_event_elapsed_time(
                        slot["copy_start"], slot["done"], synchronize=False
                    )
                else:
                    copied = perf_counter()
                    sample = slot["host"].copy()
                    self.host_copy_ms = (perf_counter() - copied) * 1000
                    self.gather_ms = cpu_gather_ms
                    self.readback_ms = cpu_gather_ms
                self.enqueue_ms = enqueue_ms
                self.readback_latency_ms = (perf_counter() - started) * 1000
                filtered = perf_counter()
                poses = self.filter.update(sample)
                self.filter_ms = (perf_counter() - filtered) * 1000
                self.last_metrics = {
                    "gatherMs": self.gather_ms,
                    "readbackMs": self.readback_ms,
                    "enqueueMs": self.enqueue_ms,
                    "hostCopyMs": self.host_copy_ms,
                    "filterMs": self.filter_ms,
                    "latencyMs": self.readback_latency_ms,
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
