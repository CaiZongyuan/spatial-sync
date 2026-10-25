# Scene Factory support tooling (SF0)

This directory holds the platform-external Scene Factory tools. The gateway never
imports it and normal CI does not need network access, Blender or a GPU.

The committed support boundary is [`support.json`](support.json). It records the
pinned source revision, per-package license and BOM evidence, measured sizes,
adaptation and import results, usage decisions and the fixed Blender/MCP
combination. Unknown or unsupported cases are recorded as gaps, never as passes.

## Commands

```bash
# GPU-free CI path: synthetic source -> adapter -> existing import -> S1–S7.
uv run --no-sync python factory/verify.py check

# Real pinned sources. Downloads are cached; this is not a CI prerequisite.
uv run --no-sync python factory/verify.py evidence \
  --cache .scratch/sf0/cache --out .scratch/sf0/evidence \
  --blender-evidence .scratch/sf0/evidence/blender.json \
  --support-out factory/support.json

# Blender and MCP for Blender support combination.
uv run --no-sync python factory/verify.py blender \
  --blender .scratch/tools/blender/blender-4.5.9-windows-x64/blender.exe \
  --mcp .scratch/tools/mcp-venv/Scripts/mcp-for-blender.exe \
  --out .scratch/sf0/evidence/blender.json
```

`evidence` writes raw reports under `<out>/reports/`, adapted packages under
`<out>/adapted/`, and a sanitized support list at `<out>/support.json`. Raw
probe output and downloaded packages stay in `.scratch/` and are not committed.
Real source evidence needs the simulation/USD extras and Pillow because texture
handling and the usage decision use them. Use `uv run --with pillow --no-sync`
or a local environment that already has them; do not change `uv.lock`, because
captured scene previews include its digest as a build input.

## Scene authoring (SF3)

`factory/plans/` holds canonical scene plans. `authoring_request` turns a plan
into the Blender workspace request (trusted asset stage paths, procedural visual
meshes, stable instance IDs and dressing). `build` writes the immutable asset and
scene packages; a layout manifest from Blender or another editor supplies poses
only, and must match the plan content hash and instance identities.

```python
from factory.authoring import authoring_request, build, load_plan

plan = load_plan("factory/plans/tube-rack-demo-v2.yaml")
request = authoring_request(plan, library_roots=[my_library])
result = build(plan, out, library_roots=[my_library], layout=layout)
```

Run `factory/blender_author.py` through Blender 4.5.9 background Python. Without
`--open-blend` it creates and records a scripted workspace; with `--open-blend`
it opens a saved human edit, rejects changed procedural bounds, and records the
`human-edited` mode plus the saved `.blend` hash. Blender imports trusted USD only
as a visual reference; the pure generator writes the OpenUSD `library:`
references, so no Blender round trip is assumed to preserve physics.

The demo plans pin a synthetic six-hole rack: version 1 uses one solid
collision box, version 2 rebuilds collision around every hole. The gateway runs
the actual S/P checks; a visual hole that keeps a sealed collision is the
intended failure case. Real Blender, Newton and viewport receipts stay under
`.scratch/`; `tests/unit/test_scene_authoring.py` is the GPU-free CI lane.

## Restricted derivation

Supported inputs are exactly the five pinned source packages in `simready.py`, at
revision `524747d9b5e1f21b5b18b56041788c01d745f1a1`. The adapter:

- verifies the package manifest, BOM hashes, license file and declared root USD;
- selects the `Newton` variant and disables MuJoCo/PhysX;
- flattens the composition under `/Asset`, checks Z-up and metersPerUnit=1, and
  rewrites all asset paths to package-relative files;
- derives `visual.glb` from `UsdPreviewSurface` materials and the ORM texture
  convention (G roughness, B metallic);
- preserves collision, mass, inertia, joints and the original license text, and
  writes `NOTICE` and `provenance.json`.

Explicitly unsupported: MDL shading (the UsdPreviewSurface fallback is used),
UDIM/UVTILE texture sets, mesh simplification, non-SimReady sources, arbitrary
URLs/paths, and packages that do not fit the existing 128 MiB upload or 256 MiB
unpack limits. The last two are SF2 prerequisites, not silently relaxed here.

## Blender baseline

| Component | Pin |
|---|---|
| Blender | 4.5.9 LTS (portable Windows archive) |
| MCP for Blender server | 2.2.2, commit `5a3618fccb2d982d0dd61e7a1879912c3100073b` |
| Addon `bl_info` | 1.8 in the same commit |
| Safe mode | `BLENDER_MCP_SAFE_MODE=1` |
| Telemetry | `DISABLE_TELEMETRY=true`; addon content telemetry consent false; runtime is recorded unavailable because the 2.2.2 wheel omits `blender_mcp.config` |
| Auto-start | addon default is true; the factory baseline disables it and the socket runs only during authoring |
| Licenses | Blender GPL-3.0-or-later; MCP for Blender and addon MIT; source package and MDL attachment licenses are recorded separately |
| Third-party providers | Poly Haven, Hyper3D, Hunyuan3D, Sketchfab, Tripo, Meshy and Poly Pizza all off |

The MCP probe uses the official client and asserts that `execute_blender_code`
rejects `import os` before the socket stage and lets a `bpy` script reach the
connection stage. The Blender probe registers the addon in background mode,
checks the defaults, exports a small USD and confirms the interactive server is
not started. The installed 2.2.2 wheel omits `blender_mcp.config`, so telemetry
cannot initialize; the disable flag is still checked with a stub config, but the
support list records the runtime as unavailable and `telemetryDisabled` as false.

Safe mode is not a sandbox: the addon socket accepts code from any local process.
It only guards the MCP path, as stated by the fixed upstream source.
