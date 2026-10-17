# Runner-owned native Newton viewer

The native viewer is an optional, local debug view of a Session's existing
Newton Runner. `NewtonSource.open_viewer()` sends an RPC to that Runner; it does
not create another simulation. Repeated opens reuse the live server and return
its loopback URL and the actual Runner PID. The platform owns Pause, Step,
Reset, invocation, reload, and stop.

## Ownership and compatibility

The Runner passes its existing `NewtonSimulation` to `NativeViewer`. It logs the
current `simulation.state` after the physics/control boundary, including while
paused. With no browser clients, the adapter returns before `begin_frame`,
`log_state`, and `end_frame`. Connected clients receive frames at up to 30 Hz.

The gateway imports neither Newton, Warp, nor Viser. SDK imports and all viewer
operations occur in the spawned Runner. Newton 1.7.0 does not accept a host or
server in `ViewerViser`; a local subclass overrides `_get_viser` to inject one
owned `ViserServer(host="127.0.0.1", port=0)`. Neither SDK classes nor module
globals are patched. The adapter requires Newton 1.7.0, Viser 1.1.1, and Viser's
installed, prebuilt client. Unsupported versions or absent optional packages
produce an optional viewer error; physics stays available.

The private SDK seams are contained in `viewer_adapter.py`:

- `_get_viser`, `_simulation_gui_handles`, and `_viewer_option_handles` remove
  native Pause/Step/Reset and force-picking controls. Picking is pinned off and
  the Runner never calls the viewer's force application API.
- Viser's server thread supplies a liveness check. An exited thread cannot
  advertise a ready URL or an invented client count.
- Viser 1.1.1 stops its executor without joining callbacks. Teardown additionally
  joins that executor before the Runner releases its model and state.

`share=False` alone does not disable Viser's browser sharing request. The
adapter also hides its Share button and overrides `request_share_url` on the
owned server subclass to return `None`. It exposes no public tunnel.

Viewer construction, rendering, and close failures are isolated from physics.
Runner teardown always proceeds through the extractor, simulation, frame pipe,
and control pipe. Closing or replacing the Source terminates the server with
its owning process. Source failure invalidates cached viewer URLs. Metrics use
the real process PID while loading and `str(simulation.model.device)` once
ready; unknown device or client count stays unknown.

## Verification

Use the existing simulation environment without syncing packages into a live
environment:

```powershell
python -m pytest tests/unit/test_native_viewer.py tests/unit/test_runner_transport.py tests/unit/test_source_state.py -q -W error
python -m pytest tests/sim_runtime/test_native_viewer.py -q -W error
```

The optional CPU regression verifies concurrent/repeated opens on one Runner,
HTTP access, platform step/reset behavior, graceful replacement, forced Runner
termination, stale-link invalidation, and reader/process/listener cleanup. Unit
coverage checks idle frame suppression, exact model/state forwarding, optional
failure isolation, loopback binding, disabled sharing/physics controls, and
gateway imports with engine/viewer imports blocked.

Real robot/browser evidence is recorded separately from unit assertions and
performance evidence. The diagnostic wrapper delegates to production rendering
and only collects observations; observer failures must never change viewer
lifetime. Validation of native shape poses uses body-world multiplied by each
shape's local transform. Native geometry poses are not body origins.
