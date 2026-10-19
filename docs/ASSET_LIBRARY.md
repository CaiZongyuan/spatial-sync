# Asset packages

Use the installed CLI without an active session:

```powershell
lw asset ls --kind robot --json
lw asset show robot/fr3@1 --json
lw asset import ./my-asset --json
```

Built-in assets live under `library/assets/`; imports go to `LW_LIBRARY`
(default `~/.spatial-sync/library`). An ID is `kind/name@rev`, where kind is
`robot`, `device`, `labware` or `environment`. Reads without a revision select
the latest version. Revisions are immutable; changed bytes create another
revision, while identical content returns the existing one. A full ID present
in both roots prevents gateway startup.

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

The gateway generates `validation.json` with S1 schema results. Remaining
structure and physics checks have **not run**. A supplied report cannot claim
passes: omit `validation.json` from a new package. An exact exported version,
including its generated report, can be imported again and deduplicates.

`contentHash` covers every published file, including the generated report:
relative paths sorted lexically, each UTF-8 path plus a zero byte followed by
the file's SHA-256 digest, all fed into SHA-256. The report's
`importContentHash` records the original upload tree for deduplication.

`robot/fr3@1` derives from the platform's pinned FR3 URDF and meshes, with an
origin-relative Z-up USD and GLB, original Apache license and source/derivative
hashes. Its report does not establish physics validity or a runnable USD scene.
The original URDF mimic relationship is retained as metadata; a USD mimic drive
has not been authored.

The command table also exposes generic `library_search`, `library_describe` and
`library_import` MCP tools. Search defaults to scenes; use `resource="assets"`,
`kind`, `query` or `gaps` for assets. `library_import.directory` names a directory
on the MCP client's filesystem. CLI equivalents include `lw library search`
and `lw library describe`.
