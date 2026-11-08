# Body-local World Building visuals

The local producer derives17 moving-body visual Assets from an already frozen
World Building frame0 export. It retains the existing physical Asset/Scene
inputs, body Prim Paths, scalar Asset.visual, HTTP resources and recording
format. Blender, source acquisition and physics adaptation are separate steps.

Raw models and derived USD/GLB packages remain in the user's local workspace and
library. The local contribution authorization covers the requested experiment
and rendered tutorial/video evidence; it does not grant public raw-asset
redistribution. Applicable Panda license, upstream identity and notices stay in
the packages. Source license, notice, attribution, README and upstream records
are copied verbatim under `upstream/` with their original relative paths,
including records outside the geometry file list. Provenance records each
source/output path, byte size and SHA256. These records stay distinct from the
local contribution authorization at the package root. Repository changes
contain the producer/tests/docs only.

With the repository's frozen USD/geometry dependencies installed, use a fresh
owned workspace outside all original inputs:

```text
python tools/worldbuilding_body_visuals.py build --project <cell-passaging-lab> --derived <frozen-derived> --inventory <blend-inventory.json> --body-map <body-anchor-map.json> --physics <qualified-canonical-package> --workspace <fresh-local-output>
python tools/worldbuilding_body_visuals.py install --workspace <same-local-output> --url http://127.0.0.1:8084
```

`derived` is the current final-lookdev frame0 output from the static visual
intake. Its derivation.json, package files and original blend hash must agree.
The inventory contains sourceSha256 and source object world matrices. The body
map contains sourceFile, frame:0 and rows with sourceBody, dynamic, visualRoot
and bodyPrimPath. It selects exactly17 unique moving bodies in panda_rail,
incubator, imager and mother. Two fixed cabinet/housing bodies retain their
canonical native visuals. This is the bounded first-transfer map, not a generic
body-name guesser.

For each selected body, the producer verifies independent source surface and
transform witnesses, uses the original trueframe0 body root to obtain body-local
geometry, and retains canonical meshes under /Asset/Appearance. No mass, joint,
filter or root transform opinion is added. Non-ASCII shader prim names are
normalized with connection retargeting and a semantic equality check. The GLB
default scene contains only that body subtree; unreachable original payloads
remain deliberately retained, so this is not a compact GLTF exporter.

The immutable base physical package must already have its own qualification.
In particular, standalone world-root doors/drawers need the explicit body
ArticulationRootAPI and a compatible reader. A visual overlay cannot repair
physical pose projection. Build/install adds no device behavior or physical
qualification. The final Scene copies its base metadata, device bindings and
Scene-local support bytes. It uses actual returned physical and visual Asset
versions, and sets Appearance inactive only in the Scene so high-poly canonical
appearance does not enter the Newton model. The visual leaf itself retains its
complete canonical data.

`body-visuals.json`, per-leaf provenance and imports.json record exact inputs,
source ownership, surface/bounds audits and actual import/validation results.
An import or validation failure stops installation; old versions and receipts
remain immutable. This command does not launch a Source. Inspect the actual
Catalog/state and use the ordinary Plan workflow for later device work.

The17-body engineering gate separately checks baseline/inactive native parity,
actual body/GLB mapping at controlled initial poses, exclusive fallback, and
recording-frozen replay after Source close. It does not establish transport,
grasp,14-step workflow or Agent success. These results and raw artifacts belong
to the issue evidence; published repository docs describe the producer's current
behavior rather than carrying private raw receipts.
