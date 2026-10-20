# Asset packages

Use the installed CLI without an active session:

```powershell
lw asset ls --kind robot --json
lw asset show robot/fr3@1 --json
lw asset import ./my-asset --json
lw asset validate robot/fr3@1 --json
lw asset rm labware/my-tube@2 --json
lw scene rm my-lab@3 --json
```

Built-in assets live under `library/assets/`; imports go to `LW_LIBRARY`
(default `~/.spatial-sync/library`). An ID is `kind/name@rev`, where kind is
`robot`, `device`, `labware` or `environment`. Reads without a revision select
the latest version. Revisions are immutable; changed bytes create another
revision, while identical content returns the existing one. A full ID present
in both roots prevents gateway startup.

`rm` requires an exact revision and only requests human approval. The Web
detail page offers the same **申请删除此版本** action. Built-in versions are
read-only. An unused user version returns `202 pending_approval` with its
approval ID; its files remain available until a person approves it in
`/approvals` or confirms `lw approve <approval-id>` in an interactive terminal.
MCP's generic `library_remove` tool can request deletion; it cannot approve it.
JSON, non-TTY and MCP approval attempts remain prohibited.

Deletion checks all retained asset and scene versions, exact templates, nested
USD dependencies, bound validation history (including legacy hashless IDs),
recordings and starting/active sessions. `409 version_in_use` lists consumers;
`409 reference_inspection_incomplete` lists objects that could not be inspected.
No deletion request is created in either case. Nonempty `tasks/`, `jobs/` or
`policies/` directories also block deletion until their product readers exist.
Approval repeats these checks and checks the original package's raw hash, so a
reference added while a request waits can block execution. The approval card
distinguishes the human decision from the execution result.

If initial source startup and cleanup both fail, the failed session remains
tracked. The 503 response supplies its session ID; inspect it and retry stopping
it before deleting retained versions. A failed state does not imply that its
resources have been released.

Approved deletion withdraws only the selected version and its indexed file
URLs. It cannot be revoked after execution. Interrupted, uncommitted withdrawal
is restored before the gateway serves its index; a committed withdrawal stays
deleted and any remaining quarantine cleanup can finish on restart. Keep the
user library's hidden transaction/revision files in backups: deleting a latest
version never makes its public revision number available for reuse. Importing
its original bytes again allocates a new revision.

The directory must contain an `asset.yaml` and its referenced USD file:

```yaml
id: labware/my-tube
kind: labware
title: My tube
stage: asset.usda
visual: visual.glb
dimensionsM: [0.02, 0.02, 0.1]
physics:
  massKg: 0.01
  massSource: estimated
  collision: authored
implements: []
dressing: false
```

Include `provenance.json`, the complete original license/notice files and all
package-local USD dependencies. Visuals, dimensions, mass and collision metadata
can be omitted while preparing an incomplete asset; they remain explicit gaps.
Malformed metadata and missing files named by `stage` or `visual` return
`422 import_invalid` with a `problems` list.

The CLI packages files from its own filesystem and uploads bytes. The HTTP
endpoint `POST /api/v1/library/imports` accepts an **uncompressed tar body** with
`Content-Type: application/x-tar`; it never accepts a gateway host path. Archives
must place `asset.yaml` at their root. Links, traversal, duplicate or ambiguous
Windows paths are rejected. Uploads are limited to 128 MiB and 4,096 entries.

The completed import job includes its ID, asset version, content hash,
deduplication result and validation report. Read jobs at `/api/v1/library/imports`
or `/api/v1/library/imports/{id}`. List assets at `/api/v1/library/assets`; detail
at `/api/v1/library/assets/{kind}/{name}/{rev}` supplies indexed file URLs.
Only indexed package files are served through `/api/v1/library/files`.

The gateway generates `validation.json` with S1–S8 structural results using a
bounded CPU-only USD worker. It checks stage units/identity, provenance and
license hashes, registered types and Config references, visual budgets and
confined USD dependencies. A 500,000-triangle or 4096-pixel texture boundary is
valid. Physics checks have **not run** and remain explicit warnings. A supplied report cannot claim
passes: omit `validation.json` from a new package. An exact exported version,
including its generated report, can be imported again and deduplicates.

The six completeness items describe collision geometry, mass and inertia, PBR
visuals, original license/source hashes, named joints/sites and implemented types.
Estimated mass is a warning. An incomplete asset remains inspectable; malformed
metadata is rejected before a revision is allocated. A structurally failed
package is imported with its failed report.

In the Web app, open `/assets` for cards or a list. Kind, gap-only, search and
view selections are shareable query parameters, for example
`/assets?kind=robot&gaps=true&q=Franka&view=list`. Thumbnails share one queued
renderer and a bounded cache. A package preview image takes precedence over
its GLB; unavailable previews show an explicit fallback.

Open `/assets/{kind}/{name}/{rev}` to inspect that immutable version. The page
shows the package's static visual, the six completeness items, binding templates,
dimensions, original provenance/license files and implemented entity types,
including their properties, actions and preconditions. This page does not start
a session or execute device actions. Missing visuals and failed loads remain
visible, with retry for load failures.

The HTTP detail response's `referencingScenes` contains indexed scene versions
whose parsed bindings name this exact asset template or whose recorded USD
dependency report names its exact ID and content hash. Reading detail does not
run validation. `referenceInspection.complete` is false when a scene package
changed, its USD dependencies are unrecorded or inspection failed;
`uninspectedScenes` supplies the affected versions and reasons. Confirmed
references are still returned when other dependencies could not be inspected.
An empty reference list establishes no references only when inspection is
complete. Without a scene index, the response explicitly reports inspection
as unavailable.

`lw asset validate kind/name@rev` (or
`POST /api/v1/library/assets/{kind}/{name}/{rev}/validate`) appends a report under
`SPATIAL_STATE_ROOT`, keyed by subject and published content hash. The embedded
report and all published bytes stay unchanged. The latest matching report is
read after a restart. CLI validation exits 3 when any check fails. The versioned
report schema is [validation.schema.json](../packages/library/schemas/validation.schema.json).

`contentHash` covers every published file, including the generated report:
relative paths sorted lexically, each UTF-8 path plus a zero byte followed by
the file's SHA-256 digest, all fed into SHA-256. The report's
`importContentHash` records the original upload tree for deduplication.

`robot/fr3@1` derives from the platform's pinned FR3 URDF and meshes, with an
origin-relative Z-up USD and GLB, original Apache license and source/derivative
hashes. Its report does not establish physics validity or a runnable USD scene.
The original URDF mimic relationship is retained as metadata; a USD mimic drive
has not been authored.

Eight small binding-reference assets accompany `lab-demo@3` and
`robot-and-tubes@2`: a Synthetic arm hand proxy, centrifuge housing and rotor-site
proxy, Synthetic tube/incubator/cabinet/sensor proxies, and two native tube shape
variants. Their `asset.usda` files contain actual primitive dimensions and
visual-local poses extracted from the pure Synthetic builder or a retained CPU
Manifest. Each `source/geometry.json` identifies the exact source path, Git
revision, raw file hash and extracted values; `provenance.json` hashes the local
files and records the producer and tools. Native tube variants retain the
30 mm cylinder/box and capsule geometries separately.

These packages supply binding declarations and partial geometry. World placement,
complete articulation, collision, mass/inertia, GLB and redistribution license
are absent. Their reports retain S4 failure for the missing license and explicit
completeness gaps, with physics not run. A scene's existing Python builder remains
the spatial authority; resolving its binding template does not certify the asset
as a complete physical model. The centrifuge's reference geometry/sites derive
from Synthetic; its native program requires scene-owned joint/resource overrides.

The reference producer identifies its exact input revision separately from its
own file hash. Reproduce this historical extraction in an isolated checkout of
`91c84a9380076cbdcf5a06d1a4ecbd1b67e81a9d`: that commit contains the recorded
producer and all five input files with the exact bytes from source revision
`882607f56e07464c14e839f2e39832d358c06062`. The current native preview cache is a
newer derived output and is not the historical input recorded by the assets.

From the primary checkout, with the frozen base/USD dependencies installed:

```powershell
git worktree add --detach .worktrees/reference-source 91c84a9380076cbdcf5a06d1a4ecbd1b67e81a9d
uv run --no-sync python .worktrees/reference-source/tools/package_binding_references.py --output .scratch/reference-output
```

Use a fresh output directory. The producer verifies the frozen input bytes, starts
no Newton source and refuses to overwrite an existing reference version.
Structural reports are generated separately through the same
CPU-only validator used by imports. Published package bytes, including reports,
remain immutable. Template selection and scene Config overrides are documented
in [Scene versions](SCENE_LIBRARY.md#binding-templates).

The command table also exposes generic `library_search`, `library_describe` and
`library_import` and `library_validate` MCP tools. Search defaults to scenes; use `resource="assets"`,
`kind`, `query` or `gaps` for assets. `library_import.directory` names a directory
on the MCP client's filesystem. CLI equivalents include `lw library search`
and `lw library describe`.
