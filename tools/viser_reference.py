"""Render the exact registered Newton scene through the upstream ViewerViser backend."""

import argparse
import sys
import time
from functools import partial
from pathlib import Path
from types import ModuleType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from newton.viewer import ViewerViser

from packages.sim_runtime.simulation import NewtonSimulation


class LocalViewerViser(ViewerViser):
    @classmethod
    def _get_viser(cls):
        # Newton 1.7.0 exposes port but not host. Keep the reference server local
        # without changing the shared viser module or Newton's visualization.
        upstream = super()._get_viser()
        local = ModuleType(upstream.__name__)
        local.__dict__.update(vars(upstream))
        local.ViserServer = partial(upstream.ViserServer, host="127.0.0.1")
        return local


def main():
    parser = argparse.ArgumentParser(description="Newton reference view at http://127.0.0.1:8080")
    parser.add_argument("scene", nargs="?", default="falling-boxes")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--frames", type=int, default=0, help="0 keeps running; positive values give bounded smoke"
    )
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()
    if args.fps < 1 or args.frames < 0:
        parser.error("fps must be positive and frames nonnegative")
    viewer = LocalViewerViser(port=args.port, share=False)
    try:
        if viewer.server.get_port() != args.port:
            raise RuntimeError(f"Reference port {args.port} is unavailable; choose an explicit free --port")
        with NewtonSimulation(args.scene, device=args.device) as sim:
            viewer.set_model(sim.model)
            frame = 0
            while viewer.is_running() and (not args.frames or frame < args.frames):
                started = time.monotonic()
                viewer.begin_frame(sim.sim_time)
                viewer.log_state(sim.state)
                viewer.end_frame()
                sim.step(max(1, round(1 / (args.fps * sim.physics_dt))))
                frame += 1
                time.sleep(max(0, 1 / args.fps - (time.monotonic() - started)))
            print(
                f"Reference complete: {frame} frames, simTime={sim.sim_time:.6f}, "
                f"solver={type(sim.solver).__name__}"
            )
    except KeyboardInterrupt:
        pass
    finally:
        viewer.close()


if __name__ == "__main__":
    main()
