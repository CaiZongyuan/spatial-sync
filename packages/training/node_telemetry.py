"""Bounded GPU history from accepted node heartbeats; missing samples stay missing."""

import math

WINDOW_SECONDS = 3600
SAMPLE_INTERVAL_SECONDS = 10


class NodeTelemetryHistory:
    def __init__(self):
        self._instances = {}
        self._points = {}

    def observe(self, node_id, instance_id, gpus, received_at):
        if self._instances.get(node_id) != instance_id:
            self._points = {key: points for key, points in self._points.items() if key[0] != node_id}
            self._instances[node_id] = instance_id
        identities = {gpu["id"] for gpu in gpus}
        self._points = {
            key: points for key, points in self._points.items()
            if key[0] != node_id or key[2] in identities
        }
        for gpu in gpus:
            observed = gpu.get("observedAt")
            reason = gpu.get("unavailableReason")
            valid_time = (
                isinstance(observed, (int, float)) and not isinstance(observed, bool)
                and math.isfinite(observed) and received_at - WINDOW_SECONDS <= observed <= received_at + 10
            )
            if valid_time:
                at = observed
                utilization, memory = gpu.get("utilization"), gpu.get("memoryUsedMb")
                if utilization is None or memory is None:
                    reason = reason or "节点未提供全部 GPU 遥测"
            else:
                at = received_at
                observed, utilization, memory = None, None, None
                reason = reason or "节点未提供有效的 GPU 采样时间"
            points = self._points.setdefault((node_id, instance_id, gpu["id"]), {})
            bucket = math.floor(at / SAMPLE_INTERVAL_SECONDS)
            previous = points.get(bucket)
            if previous is None or at >= previous["at"]:
                points[bucket] = {
                    "at": at, "observedAt": observed, "utilization": utilization,
                    "memoryUsedMb": memory, "unavailableReason": reason,
                }
            self._prune(points, received_at)

    @staticmethod
    def _prune(points, now):
        for bucket in list(points):
            if points[bucket]["at"] < now - WINDOW_SECONDS:
                del points[bucket]
        # Includes both endpoints when the window straddles an exact bucket edge.
        for bucket in sorted(points)[:max(0, len(points) - WINDOW_SECONDS // SAMPLE_INTERVAL_SECONDS - 1)]:
            del points[bucket]

    def for_gpu(self, node_id, instance_id, gpu_id, now):
        points = self._points.get((node_id, instance_id, gpu_id), {})
        self._prune(points, now)
        samples = [dict(points[bucket]) for bucket in sorted(points)]
        return {
            "windowSeconds": WINDOW_SECONDS, "sampleIntervalSeconds": SAMPLE_INTERVAL_SECONDS,
            "startedAt": samples[0]["at"] if samples else None, "samples": samples,
        }
