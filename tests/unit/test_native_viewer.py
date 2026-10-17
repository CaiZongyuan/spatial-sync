"""Native view ownership/error behavior without importing the engine or Viser."""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from apps.api.app.sources.newton_source import NewtonSource
from packages.sim_runtime import viewer_adapter
from packages.sim_runtime.runner import _Runner


class FakeViewer:
    def __init__(self):
        self.clients, self.calls = {}, []
        self.running = True
        self.server = SimpleNamespace(
            get_clients=lambda: self.clients.copy(),
            get_port=lambda: 43210,
            stop=lambda: self.calls.append("server.stop"),
            _thread_executor=SimpleNamespace(
                shutdown=lambda **kwargs: self.calls.append(("shutdown", kwargs))
            ),
            _websock_server=SimpleNamespace(_server_thread=SimpleNamespace(is_alive=lambda: self.running)),
        )

    def set_model(self, model):
        self.model = model

    def begin_frame(self, sim_time):
        self.calls.append(("begin", sim_time))

    def log_state(self, state):
        self.calls.append(("state", state))

    def end_frame(self):
        self.calls.append("end")

    def close(self):
        self.calls.append("viewer.close")


def test_idle_viewer_skips_frame_work_and_connected_view_uses_current_model_and_state(monkeypatch):
    backend = FakeViewer()
    monkeypatch.setattr(viewer_adapter, "_create_viewer", lambda _: backend)
    simulation = SimpleNamespace(model=object(), state=object(), sim_time=2)
    viewer = viewer_adapter.NativeViewer(simulation, "session")
    assert backend.model is simulation.model
    assert viewer.stats()["clientCount"] == 0
    assert not viewer.render(10, force=True) and not backend.calls
    backend.clients[1] = object()
    assert viewer.render(10)
    assert backend.calls == [("begin", 2), ("state", simulation.state), "end"]
    assert not viewer.render(10.01)
    simulation.state, simulation.sim_time = object(), 3
    assert viewer.render(10.01, force=True)
    assert backend.calls[-3:] == [("begin", 3), ("state", simulation.state), "end"]
    backend.clients.clear()
    frames = viewer.published_frames
    assert not viewer.render(20) and viewer.published_frames == frames
    viewer.close()
    viewer.close()
    assert backend.calls.count("viewer.close") == 1
    assert "server.stop" in backend.calls and ("shutdown", {"wait": True}) in backend.calls


def test_model_registration_failure_closes_server_and_callbacks(monkeypatch):
    backend = FakeViewer()

    def fail_model(_):
        raise RuntimeError("unsupported model")

    backend.set_model = fail_model
    monkeypatch.setattr(viewer_adapter, "_create_viewer", lambda _: backend)
    with pytest.raises(RuntimeError, match="unsupported model"):
        viewer_adapter.NativeViewer(SimpleNamespace(model=object()), "session")
    assert "viewer.close" in backend.calls and "server.stop" in backend.calls
    assert ("shutdown", {"wait": True}) in backend.calls


def test_exited_server_reports_unknown_client_count_and_cannot_advertise_a_ready_url(monkeypatch):
    backend = FakeViewer()
    monkeypatch.setattr(viewer_adapter, "_create_viewer", lambda _: backend)
    viewer = viewer_adapter.NativeViewer(SimpleNamespace(model=object()), "session")
    backend.running = False
    assert viewer.stats() == {"status": "unavailable", "url": None, "clientCount": None,
                              "reason": "Native viewer server is not running"}
    with pytest.raises(RuntimeError, match="server is not running"):
        viewer.render(0)
    viewer.close()


def test_sdk_adapter_owns_loopback_server_removes_physics_controls_and_blocks_sharing(monkeypatch, tmp_path):
    client = tmp_path / "viser" / "client" / "build" / "index.html"
    client.parent.mkdir(parents=True)
    client.touch()
    calls, handles = [], []

    def handle():
        item = SimpleNamespace(removed=False)
        item.remove = lambda: setattr(item, "removed", True)
        handles.append(item)
        return item

    class Server:
        def __init__(self, **options):
            calls.append(options)
            self.gui = SimpleNamespace(
                configure_theme=lambda **options: calls.append(options),
                add_markdown=calls.append,
            )

        def get_host(self):
            return "127.0.0.1"

        def get_port(self):
            return 43210

        def request_share_url(self):
            return "https://public.example/"

    class SDKViewer:
        @classmethod
        def _get_viser(cls):
            return "original factory"

        def __init__(self, **options):
            self.server = self._get_viser().ViserServer(**options)
            self.picking_enabled = True
            self._setup_gui()

        @property
        def picking_enabled(self):
            return self._picking_enabled

        @picking_enabled.setter
        def picking_enabled(self, enabled):
            self._picking_enabled = enabled

        def _setup_gui(self):
            self._simulation_gui_handles = {name: handle() for name in ("pause", "step", "reset")}
            self._viewer_option_handles = {"picking_enabled": handle()}

    monkeypatch.setitem(sys.modules, "viser", SimpleNamespace(__file__=str(client.parents[2] / "__init__.py"),
                                                            ViserServer=Server))
    monkeypatch.setitem(sys.modules, "newton.viewer", SimpleNamespace(ViewerViser=SDKViewer))
    monkeypatch.setattr(viewer_adapter, "version", lambda name: {"newton": "1.7.0", "viser": "1.1.1"}[name])
    viewer = viewer_adapter._create_viewer("owned-session")
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 0
    assert {"show_share_button": False} in calls
    assert viewer.server.request_share_url() is None
    assert Server().request_share_url() == "https://public.example/"
    assert SDKViewer._get_viser() == "original factory"
    assert not viewer.picking_enabled and all(item.removed for item in handles)
    assert not viewer._simulation_gui_handles and not viewer._viewer_option_handles
    assert "Simulation control stays in Spatial Sync." in calls


def test_open_requests_reuse_one_viewer_on_the_existing_runner_simulation(monkeypatch):
    runner = _Runner.__new__(_Runner)
    runner.sim, runner.session_id, runner.viewer, runner._viewer_error = object(), "session", None, None
    packets, created = [], []
    runner.control = SimpleNamespace(offer=packets.append)
    runner.stats = lambda: {"viewer": runner._viewer_stats()}

    def create(simulation, session_id):
        created.append((simulation, session_id))
        return SimpleNamespace(stats=lambda: {"status": "ready", "url": "http://127.0.0.1:43210/"})

    monkeypatch.setattr(viewer_adapter, "NativeViewer", create)
    for token in ("first", "second"):
        assert runner._handle({"kind": "open_viewer", "token": token})
    assert created == [(runner.sim, "session")]
    assert packets[0]["viewer"] == packets[1]["viewer"] == {
        "status": "ready", "url": "http://127.0.0.1:43210/", "backendPid": os.getpid()
    }


def test_viewer_render_failure_is_scoped_and_cannot_prevent_physics_teardown():
    runner = _Runner.__new__(_Runner)
    calls = []

    def fail_render(_):
        raise RuntimeError("viewer transport failed")

    def fail_close():
        calls.append("viewer.close")
        raise RuntimeError("viewer close failed")

    runner.viewer = SimpleNamespace(render=fail_render, close=fail_close)
    runner._render_viewer(0)
    assert runner.viewer is None and "viewer transport failed" in runner._viewer_error
    runner.viewer = SimpleNamespace(close=fail_close)
    runner.extractor = SimpleNamespace(close=lambda: calls.append("extractor.close"))
    runner.sim = SimpleNamespace(close=lambda: calls.append("sim.close"))
    runner.frames = SimpleNamespace(close=lambda: calls.append("frames.close"))
    runner.control = SimpleNamespace(close=lambda **_: calls.append("control.close"))
    with pytest.raises(RuntimeError, match="viewer close failed"):
        runner.close()
    assert calls[-4:] == ["extractor.close", "sim.close", "frames.close", "control.close"]


def test_viewer_stats_failure_is_scoped_when_open_error_also_reports_runner_metrics(monkeypatch):
    runner = _Runner.__new__(_Runner)
    closed, packets = [], []

    def broken_stats():
        raise RuntimeError("viewer client list failed")

    def broken_viewer(*_):
        return SimpleNamespace(stats=broken_stats, close=lambda: closed.append("viewer.close"))

    runner.viewer = broken_viewer()
    runner._viewer_error, runner.session_id = None, "stats-error"
    runner.info = SimpleNamespace(stats=lambda: {})
    runner.sim = SimpleNamespace(model=SimpleNamespace(device=SimpleNamespace(is_cuda=False),
                                                      body_count=40, shape_count=117),
                                 solver_ms=0, paused=True)
    runner.extractor = SimpleNamespace(
        readback_ms=0, readback_latency_ms=0, enqueue_ms=0, host_copy_ms=0, filter_ms=0,
        last_metrics={}, readback_mode="cpu-bulk", dropped_captures=0,
        filter=SimpleNamespace(total_invalid=0, consecutive_invalid=SimpleNamespace(tolist=lambda: [])),
    )
    runner.frames = SimpleNamespace(slot=SimpleNamespace(overwritten=0))
    runner.pacer = SimpleNamespace(dropped_wall_seconds=0)
    runner._heartbeat_seq, runner._heartbeat_at = 1, 0
    runner.publish_ms, runner.step_hz, runner.actual_rtf = 0, 0, 0
    runner.control = SimpleNamespace(offer=packets.append)
    monkeypatch.setattr(viewer_adapter, "NativeViewer", broken_viewer)
    assert runner._handle({"kind": "open_viewer", "token": "open"})
    assert packets[-1]["kind"] == "viewer_error"
    assert "viewer client list failed" in packets[-1]["message"]
    assert runner.viewer is None and closed == ["viewer.close", "viewer.close"]
    assert runner.stats()["viewer"]["status"] == "unavailable"


async def test_viewer_rpc_failure_does_not_fail_the_source_or_leave_a_ready_link():
    source = NewtonSource("viewer-error")
    future = asyncio.get_running_loop().create_future()
    source._pending["viewer"] = future
    source._on_control({"kind": "viewer_error", "token": "viewer", "message": "optional dependency missing",
                        "stats": {"viewer": {"status": "unavailable", "url": None,
                                              "clientCount": None, "reason": "optional dependency missing"}}})
    with pytest.raises(RuntimeError, match="optional dependency missing"):
        await future
    assert not source._failed and source.stats()["viewer"]["clientCount"] is None


def test_loading_reports_actual_process_identity_and_failed_runner_invalidates_stale_viewer_url():
    source = NewtonSource("loading")
    source._process = SimpleNamespace(pid=123, is_alive=lambda: True)
    assert source.stats()["runnerPID"] == 123 and source.stats()["runnerAlive"]
    assert source.stats()["device"] is None and source.stats()["viewer"]["status"] == "not_started"
    source._metrics["viewer"] = {"status": "ready", "url": "http://127.0.0.1:43210/",
                                "clientCount": 1, "reason": None}
    source._failed, source._failure_reason = True, "runner disconnected"
    assert source.stats()["viewer"] == {"status": "unavailable", "url": None, "clientCount": None,
                                         "reason": "runner disconnected"}


def test_gateway_imports_source_runner_and_adapter_without_engine_or_viser_dependencies():
    code = """
import importlib.abc, sys
class NoEngine(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('newton', 'warp', 'viser'):
            raise AssertionError('Engine import in gateway: ' + name)
sys.meta_path.insert(0, NoEngine())
from apps.api.app.sources.newton_source import NewtonSource
from packages.sim_runtime.runner import run_runner
from packages.sim_runtime.viewer_adapter import NativeViewer
assert NewtonSource('gateway').stats()['viewer']['status'] == 'not_started'
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
