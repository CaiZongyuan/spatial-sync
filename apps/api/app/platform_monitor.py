"""Cached gateway-host metrics. All OS probes run in one background worker."""

from __future__ import annotations

import asyncio
import csv
import io
import math
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import psutil

INTERVAL_SECONDS = 2


def unavailable_system(reason):
    return {
        "cpu": {"percent": None, "logicalCores": None, "reason": reason},
        "memory": {
            "totalBytes": None, "usedBytes": None, "availableBytes": None, "percent": None, "reason": reason,
        },
        "gpus": [], "gpuStatus": "unavailable", "gpuMessage": reason,
    }


def session_rows(manager):
    """Copy only owned state on the event loop; never retain a live Session in a worker."""
    rows = []
    for session in manager.platform_sessions():
        backend = None
        if session.kind == "newton":
            source = session._starting_source or session.source
            stats, reason = {}, "Newton backend is not running"
            if source is not None:
                try:
                    stats = source.stats()
                    reason = None
                except Exception as exc:
                    reason = f"Newton backend statistics unavailable: {exc}"
            backend = {
                "pid": stats.get("runnerPID"),
                "alive": stats.get("runnerAlive") is True,
                "device": stats.get("device"),
                "cpuPercent": None, "memoryBytes": None, "metricsReason": reason,
                "viewer": dict(stats["viewer"]) if stats.get("viewer") else {
                    "status": "unavailable", "url": None, "clientCount": None,
                    "reason": reason or "This adapter does not report Native Viewer status",
                },
            }
        rows.append({
            "sessionId": session.session_id, "sceneId": session.scene_id,
            **({"evaluationId": session.evaluation_id} if getattr(session, "evaluation_id", None) else {}),
            "sceneVersion": session.scene_version.id if getattr(session, "scene_version", None)
            else f"{session.scene_id}@1",
            "sceneName": session.scene_version.metadata["title"] if getattr(session, "scene_version", None)
            else session.scene_id, "source": session.kind,
            "state": session.state, "connectedClients": len(session.clients),
            "simTime": session.sim_time, "actualRtf": session.actual_rtf if session.state == "RUNNING" else 0,
            "backend": backend,
        })
    return rows


def platform_snapshot(rows, system):
    return {
        "sampledAtMs": int(time.time() * 1000), "samplingIntervalSeconds": INTERVAL_SECONDS,
        "summary": {
            "totalSessions": len(rows),
            "runningSessions": sum(row["state"] == "RUNNING" for row in rows),
            "pausedSessions": sum(row["state"] == "PAUSED" for row in rows),
            "connectedSessions": sum(row["connectedClients"] > 0 for row in rows),
            "connectedClients": sum(row["connectedClients"] for row in rows),
            "newtonBackends": sum(bool(row["backend"] and row["backend"]["alive"]) for row in rows),
        },
        "sessions": rows, "system": system,
    }


class HostSampler:
    def __init__(self):
        self._cpu_primed = False
        self._processes = {}

    def sample(self, rows):
        system = unavailable_system("Host metrics unavailable")
        try:
            percent = psutil.cpu_percent(interval=None)
            cores = psutil.cpu_count()
            reason = None if self._cpu_primed else "CPU usage needs two background samples"
            system["cpu"] = {
                "percent": percent if self._cpu_primed else None,
                "logicalCores": cores,
                "reason": reason or ("Logical CPU count unavailable" if cores is None else None),
            }
            self._cpu_primed = True
        except (psutil.Error, OSError) as exc:
            system["cpu"]["reason"] = f"CPU metrics unavailable: {exc}"
        try:
            memory = psutil.virtual_memory()
            system["memory"] = {
                "totalBytes": memory.total, "usedBytes": memory.total - memory.available,
                "availableBytes": memory.available, "percent": memory.percent, "reason": None,
            }
        except (psutil.Error, OSError) as exc:
            system["memory"]["reason"] = f"Memory metrics unavailable: {exc}"
        system["gpus"], system["gpuStatus"], system["gpuMessage"] = self._gpus()
        current_pids = set()
        for row in rows:
            backend = row["backend"]
            if not backend or not backend["alive"] or not backend["pid"]:
                if backend:
                    backend["metricsReason"] = backend["metricsReason"] or (
                        "Owned Runner PID is unavailable"
                        if backend["alive"] else "Newton backend is not running"
                    )
                continue
            pid = backend["pid"]
            current_pids.add(pid)
            try:
                process = self._processes.get(pid)
                first = process is None
                if first:
                    process = self._processes[pid] = psutil.Process(pid)
                if not process.is_running():
                    raise psutil.NoSuchProcess(pid)
                backend["memoryBytes"] = process.memory_info().rss
                percent = process.cpu_percent(interval=None)
                backend["cpuPercent"] = None if first else percent
                backend["metricsReason"] = "CPU usage needs two background samples" if first else None
                if backend.get("device") is None:
                    reason = "Newton device has not been reported"
                    backend["metricsReason"] = "; ".join(filter(None, [backend["metricsReason"], reason]))
            except psutil.NoSuchProcess as exc:
                self._processes.pop(pid, None)
                backend["alive"] = False
                backend["cpuPercent"] = backend["memoryBytes"] = None
                backend["metricsReason"] = f"Owned Newton process exited: {exc}"
                backend["viewer"] = {
                    "status": "unavailable", "url": None, "clientCount": None,
                    "reason": "Owned Newton process exited",
                }
            except (psutil.Error, OSError) as exc:
                self._processes.pop(pid, None)
                backend["metricsReason"] = f"Backend metrics unavailable: {exc}"
        self._processes = {pid: proc for pid, proc in self._processes.items() if pid in current_pids}
        return platform_snapshot(rows, system)

    @staticmethod
    def _gpus():
        executable = shutil.which("nvidia-smi")
        if executable is None:
            return [], "unsupported", "NVIDIA monitoring is unavailable on this gateway host"
        try:
            result = subprocess.run(
                [executable,
                 "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=1, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            gpus, partial = [], False
            for fields in csv.reader(io.StringIO(result.stdout)):
                if len(fields) != 6:
                    raise ValueError("Unexpected nvidia-smi output")
                index, name, utilization, used, total, temperature = (field.strip() for field in fields)

                def number(value, multiplier=1):
                    nonlocal partial
                    try:
                        result = float(value) * multiplier
                        if math.isfinite(result):
                            return result
                    except ValueError:
                        pass
                    partial = True
                    return None

                gpus.append({
                    "index": int(index), "name": name, "utilizationPercent": number(utilization),
                    "memoryUsedBytes": number(used, 1024 * 1024),
                    "memoryTotalBytes": number(total, 1024 * 1024), "temperatureC": number(temperature),
                })
            if not gpus:
                return [], "unavailable", "nvidia-smi reported no GPUs"
            return gpus, "available", "Some GPU metrics are not supported by the driver" if partial else None
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return [], "unavailable", f"NVIDIA metrics unavailable: {exc}"


class PlatformMonitor:
    def __init__(self, manager, sampler=None):
        self.manager = manager
        self.sampler = sampler if sampler is not None else HostSampler()
        self.snapshot = platform_snapshot([], unavailable_system("Waiting for the first background sample"))
        self._stop = asyncio.Event()
        self._task = None
        self._worker = None

    async def start(self):
        if self._task is None:
            self._stop.clear()
            # psutil keeps the system CPU baseline per thread.
            self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="platform-monitor")
            self._task = asyncio.create_task(self._run(), name="platform-monitor")

    async def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            rows = []
            try:
                rows = session_rows(self.manager)
                self.snapshot = await asyncio.get_running_loop().run_in_executor(
                    self._worker, self.sampler.sample, rows
                )
            except Exception as exc:
                reason = f"Platform sampling failed: {exc}"
                for row in rows:
                    if row["backend"]:
                        row["backend"]["metricsReason"] = reason
                self.snapshot = platform_snapshot(rows, unavailable_system(reason))
            remaining = max(0, INTERVAL_SECONDS - (time.monotonic() - started))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=remaining)
            except TimeoutError:
                pass

    async def close(self):
        self._stop.set()
        if self._task is not None:
            # Await the bounded in-flight OS probe; cancellation would leave its thread behind.
            try:
                await asyncio.shield(self._task)
            except asyncio.CancelledError:
                await self._task
                raise
            finally:
                self._worker.shutdown(wait=True)
                self._worker = None
                self._task = None
