"""Runner-owned, view-only Newton 1.7 ViewerViser on an ephemeral loopback port.

Newton 1.7's private _get_viser/GUI seams are contained here. Its constructor
cannot accept a host/server; a local subclass injects one owned Viser server.
The SDK module and original class are never patched. Imports stay lazy so the
gateway can use NewtonSource without simulation/viewer dependencies installed.
"""

from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace


def _create_viewer(session_id):
    import viser
    from newton.viewer import ViewerViser

    if version("newton") != "1.7.0":
        raise RuntimeError("The native viewer adapter requires Newton 1.7.0")
    if version("viser") != "1.1.1":
        raise RuntimeError("The native viewer adapter requires Viser 1.1.1")
    package = Path(viser.__file__).resolve().parent
    if package.parent.name == "src" or not (package / "client/build/index.html").is_file():
        raise RuntimeError("The native viewer requires an installed, prebuilt Viser client")

    class LoopbackServer(viser.ViserServer):
        def request_share_url(self, *args, **kwargs):
            # Viser also offers sharing through a browser message, independently
            # of ViewerViser's share=False constructor argument.
            return None

    server = LoopbackServer(host="127.0.0.1", port=0, label=f"Newton · {session_id}", verbose=False)
    factory = SimpleNamespace(ViserServer=lambda **kwargs: server)

    class SessionViewer(ViewerViser):
        @classmethod
        def _get_viser(cls):
            return factory

        @ViewerViser.picking_enabled.setter
        def picking_enabled(self, enabled):
            # Force picking applies spring forces; only camera/view options belong here.
            ViewerViser.picking_enabled.fset(self, False)

        def _setup_gui(self):
            super()._setup_gui()
            for handle in self._simulation_gui_handles.values():
                handle.remove()
            self._simulation_gui_handles.clear()
            self._viewer_option_handles.pop("picking_enabled").remove()
            self.server.gui.add_markdown("Simulation control stays in Spatial Sync.")

    try:
        server.gui.configure_theme(show_share_button=False)
        viewer = SessionViewer(port=server.get_port(), share=False, verbose=False)
        if server.get_host() != "127.0.0.1":
            raise RuntimeError("The native viewer must bind only to loopback")
        return viewer
    except BaseException:
        _stop_server(server)
        raise


def _stop_server(server):
    try:
        server.stop()
    finally:
        # Viser 1.1.1 stop releases its executor without waiting. Our callbacks
        # are view-only; join them before releasing the Runner's model/state.
        executor = getattr(server, "_thread_executor", None)
        if executor is not None:
            executor.shutdown(wait=True)


class NativeViewer:
    def __init__(self, simulation, session_id):
        self.simulation = simulation
        self.viewer = _create_viewer(session_id)
        self.closed = False
        self.published_frames = 0
        self.next_frame_at = 0.0
        try:
            self.viewer.set_model(simulation.model)
        except BaseException:
            self.close()
            raise

    @property
    def url(self):
        return f"http://127.0.0.1:{self.viewer.server.get_port()}/"

    def stats(self):
        if self.closed or not self.viewer.server._websock_server._server_thread.is_alive():
            return {
                "status": "unavailable", "url": None, "clientCount": None,
                "reason": "Native viewer server is not running",
            }
        return {
            "status": "ready",
            "url": self.url,
            "clientCount": len(self.viewer.server.get_clients()),
            "reason": None,
        }

    def render(self, now, *, force=False):
        if self.closed:
            return False
        if not self.viewer.server._websock_server._server_thread.is_alive():
            raise RuntimeError("Native viewer server is not running")
        if not self.viewer.server.get_clients():
            self.next_frame_at = 0.0
            return False
        if not force and now < self.next_frame_at:
            return False
        self.viewer.begin_frame(self.simulation.sim_time)
        try:
            self.viewer.log_state(self.simulation.state)
        finally:
            self.viewer.end_frame()
        self.published_frames += 1
        self.next_frame_at = now + 1 / 30
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.viewer.close()
        finally:
            _stop_server(self.viewer.server)
