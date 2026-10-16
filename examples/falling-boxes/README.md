# Falling boxes and rotating joint

The registered `falling-boxes` scene is built in `packages/sim_runtime/scenes.py` with a static
Z-up ground plane, one falling box, and a pendulum revolute joint. The joint's Site is offset
from its body origin by 0.35 m. Newton XPBD is the physics authority on CPU and CUDA.

```powershell
uv sync --extra simulation --extra usd
uv run --extra simulation --extra usd python tools/smoke_newton.py --device cpu
uv run --extra simulation --extra usd python tools/smoke_newton.py --device cuda:0
uv run --extra simulation python tools/viser_reference.py falling-boxes
```

Open `http://127.0.0.1:8080` for the upstream Newton ViewerViser reference. Stop with Ctrl+C;
`--frames 60` exits and closes the server after a bounded run. The `.xml` and `.usda` files
are minimal import/API receipts, not substitutes for the running Python scene.
