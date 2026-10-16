# USD laboratory and visual bindings

`lab-y-up.usda` is an original, offline USD scene in meters with Y-up authored
coordinates. `usd_lab_builder()` imports it into a Z-up Newton builder. The
Housing is static mesh equipment; the Spindle drives a rotor; MovingPart is a
dynamic body with a visible mesh and separate primitive collider. Cable is a
BasisCurves compatibility example which is deliberately omitted with a warning.
MovingPart also contains a small visual-only Cube inside the mesh bounds, so the
native fallback group proves simultaneous mesh + primitive rendering without
changing those bounds or adding a collision shape.

Newton 1.7's `apply_up_axis_from_stage=True` adopts the stage's up axis. The loader
therefore uses **False**, which rotates the stage into the existing Z-up builder.
This corrects the earlier P0-A flag assumption; P0-A's actual fixture was Z-up and
did not discriminate these choices. Its historical test receipts are unchanged.
After import, the loader rewrites body, shape and joint labels from the returned
`path_*_map` values. Downstream code derives entityKey solely from those labels.

Independent literal checks establish MovingPart's initial world position
`(1,-0.2,1.5)` and native mesh bounds `(0.9,-0.5,1.3)` to `(1.1,0.1,1.7)` in the
protocol's Z-up meters. A 24-step public API command advances 0.1 seconds and
moves the body downward in world Z. The real Source/IPC/HTTP test reads the
generated native GLB, verifies its content hash/MIME/cache and closes the runner.

`assets/bindings/usd-lab.yaml` demonstrates two cases:

- Housing loads `equipment-cover.glb`, a 988-byte original Y-up asset generated
  by `tools/generate_demo_assets.py`. Its local rotation calibrates the asset
  basis against the imported static node's frame; the renderer applies asset
  Y-up conversion once inside that local pose. The verified world bounds are
  `(-1.1,-0.2,0)` to `(-0.5,0.2,0.6)`.
- MovingPart intentionally references an unregistered GLB. Its primary URL
  returns 404; `asset_missing` and `asset_fallback` warnings explain the issue.
  Its complete native mesh group remains in `fallbackVisuals` and is served by
  the content-hash mesh endpoint. Colliders and a placeholder remain later tiers.

The native GLB exporter preserves triangle positions/indices and available
vertex normals. It does not bake shape scale or asset-axis conversion into the
artifact. The viewer computes normals when absent. Native source textures/UV
materials and arbitrary USD material/shader schemas are not mirrored; use a
registered GLB/glTF binding for detailed appearance. This is a geometry bridge,
not lossless USD rendering. Supported primitives retain their Newton dimensions.

The loader records importer warnings, missing rigid-body imports and unsupported
geometric prim types in Manifest compatibility warnings. This fixture validates
meters and Y/Z-up rigid geometry; it does not claim every USD schema or every
external stage's material/unit/import behavior is supported.

```powershell
uv sync --frozen --extra simulation --extra usd --group dev
uv run --frozen --extra simulation --extra usd python -m pytest tests/sim_runtime/test_usd_assets.py tests/sim_runtime/test_asset_gateway.py -q
uv run --frozen --extra simulation --extra usd python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```
