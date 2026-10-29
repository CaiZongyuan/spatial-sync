"""Opt-in owned-demo raw contact receipts; never alter physics or contact data."""

import atexit
import json
import os
import queue
import threading
from pathlib import Path

_directory = os.environ.get("SPATIAL_PASSAGE_CONTACT_TRACE_DIR")
if _directory:
    from packages.sim_runtime.physics_port import PhysicsPort

    _pending = queue.Queue(maxsize=256)
    _ports = {}
    _stats = {"queued": 0, "written": 0, "dropped": 0, "writeErrors": 0}
    _end = object()
    _target = Path(_directory)
    _target.mkdir(parents=True, exist_ok=True)

    def _write():
        try:
            with (_target / f"contacts-{os.getpid()}.jsonl").open("w", encoding="utf8") as stream:
                while True:
                    item = _pending.get()
                    if item is _end:
                        break
                    stream.write(json.dumps(item, separators=(",", ":"), allow_nan=False) + "\n")
                    _stats["written"] += 1
        except (OSError, TypeError, ValueError):
            _stats["writeErrors"] += 1

    _worker = threading.Thread(target=_write, daemon=True, name="passage-contact-receipts")
    _worker.start()
    _original_init = PhysicsPort.__init__
    _original_query = PhysicsPort.collision_contacts

    def _enqueue(value):
        try:
            _pending.put_nowait(value)
            _stats["queued"] += 1
        except queue.Full:
            _stats["dropped"] += 1

    def _init(self, simulation, manifest):
        _original_init(self, simulation, manifest)
        identity = f"{manifest.sessionId}:{manifest.sceneRevision}:{id(self)}"
        _ports[id(self)] = {"identity": identity, "last": None}
        _enqueue({"type": "model", "identity": identity, "bodyKeys": list(simulation.model.body_label),
                  "shapeKeys": list(simulation.model.shape_label), "source": "captured-pose PhysicsPort"})

    def _query(self, **kwargs):
        result = _original_query(self, **kwargs)
        owned = _ports[id(self)]
        capture = (self.sim.epoch, self.last_time)
        if owned["last"] != capture:
            owned["last"] = capture
            _enqueue({"type": "contacts", "identity": owned["identity"], "simTime": self.last_time,
                      "epoch": self.sim.epoch,
                      "contacts": [[list(row.bodies), list(row.shapes), row.distance_m]
                                   for row in self._contact_cache]})
        return result

    PhysicsPort.__init__, PhysicsPort.collision_contacts = _init, _query

    def _close():
        try:
            _pending.put(_end, timeout=5)
            _worker.join(timeout=5)
            (_target / f"contacts-{os.getpid()}-receipt.json").write_text(json.dumps({
                "scope": "All cached contacts at observed captured frames, including positive margins",
                "fields": ["bodyIndices", "shapeKeys", "signedDistanceM"],
                "physicsModified": False, "complete": not _worker.is_alive()
                and not _stats["dropped"] and not _stats["writeErrors"]
                and _stats["queued"] == _stats["written"], **_stats,
            }, indent=2), encoding="utf8")
        except (OSError, queue.Full):
            pass

    atexit.register(_close)
