# Import the World Building laboratory locally

The external `cell-passaging-lab` project can supply a static, editable-source
reference in Lab World. This intake keeps the source Panda, linear rail,
incubator, imager, workstation, centrifuge, decapper and labware as separate
visual Assets. The resulting `panda-cell-passaging-reference` Scene has no
device bindings. Its static appearance does not establish the 14-step workflow.

Keep the original project and its mixed-source models outside Git. This tool
does not assert public distribution or commercial license qualification.
Blender source, linked source identity and notices remain factory-side or in
the isolated local user library. The built-in FR3 is not substituted for Panda.
Packages retain a narrow local-use contribution record based on the user's
explicit import instruction, source rights notices and file hashes. Panda's
original Apache license remains unchanged; this record grants no new rights
over unknown external content and does not qualify raw binaries for publication.

## Build the bounded packages

Use the existing frozen Python environment and Blender 5.4 (the source's version).
The source must contain `07-render/render_scene_lookdev.blend`. Its images must
be packed and it must have no linked external Blender libraries.
Retain the workspace's `assets/` tree alongside its `projects/` directory for attribution inputs:
Menagerie Panda's LICENSE, README and pinned commit, the workstation's
SOURCE_NOTICE, the centrifuge's sources/assumptions, and the imager's README.
These original records are copied and hashed in each applicable local package.

```powershell
uv run --no-sync python tools/worldbuilding_intake.py build `
  --project <cell-passaging-lab-directory> `
  --blender <blender-5.4-executable> `
  --workspace .scratch/worldbuilding-intake
```

The command evaluates the final lookdev file at **frame 0**, exports with two
CPU threads, and performs no rendering, training or device operation. It never
saves the original source. Use a new workspace for changed source bytes; a
completed derivative remains immutable.

Every assembly retains its original mesh/body anchor nodes and gets an explicit
Scene placement. Chunks contain at most 450,000 triangles, remain metres/Z-up,
and include their USD and embedded-texture GLB. Source and producer hashes,
packed image dependencies, object maps, evaluated source matrices/bounds and
material approximations live in `derived/derivation.json` and each package's
`provenance.json`. Independently decoded GLB position bytes, node transforms and
USD surface geometry are checked before packaging; `derived/audit.json` records
results. Per-object witnesses retain canonical float32 vertices and polygon
payload hashes rather than duplicating source vertex arrays in JSON. The GLB
and USD decoders compare those payloads, actual nondegenerate surface bounds
and object transforms against the evaluated source. Metadata/output paths are
checked for redirects before any package metadata is written.
Polygon witnesses preserve boundary order, allowing only cyclic start changes
and reversal; they reject a different connection of the same corner points.
The source's frame-0 camera cutaway is retained. Its ceiling, ceiling-panel
display meshes and neighboring exterior walls are omitted for the browser's
external view, with every excluded object listed. Workflow equipment is retained.
Source zero-area triangles that Blender removes are counted separately; all
nondegenerate triangles and exported mesh identities must agree.

The original `05-modelling/lab_visual.glb` is partial and Y-up. This tool uses the
full lookdev source instead. It preserves static geometry, not source animation
or MuJoCo actuator behavior. Blender procedural shading, thin-glass mixes,
Cycles lighting and volume are not baked into this intake.
Transparent/Glossy shader mixes receive recorded PBR alpha approximations so
glass remains transparent; the procedural blue band becomes an opaque blue plate.
The centrifuge's eight visible adapters do not imply eight qualified loading positions; source
physics qualified adapters 2 and 6.

## Import through the public CLI

Start an owned gateway as described in [Scene Factory](SCENE_FACTORY.md), with
its own `LW_LIBRARY`, state, recording and preview directories under `.scratch/`.
Leave existing services and Sessions to their owners. Then run:

```powershell
uv run --no-sync python tools/worldbuilding_intake.py install `
  --project <cell-passaging-lab-directory> `
  --workspace .scratch/worldbuilding-intake `
  --url http://127.0.0.1:8075
```

This calls the real `lw asset import` and `lw scene import` entrypoints. It uses
the exact returned Asset revisions to compose the Scene, retains import and
validation receipts in `imports.json`, and reads back `scene-detail.json`.
Repeating `install` with identical inputs reuses the existing immutable versions.
Any failed validation stops the command and preserves the failure receipt.

Add `--launch` to start the imported exact Scene with Newton. The returned
Session ID is saved in `session.json`; open it in the matching Web app and close
that owned Session when finished. This is a static visual Session with Dressing
only. The dynamic laboratory and Agent-control evidence have separate scope.
