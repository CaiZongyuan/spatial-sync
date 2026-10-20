# Scene versions

The gateway discovers scene packages at startup from the repository's read-only
`library/` and from `LW_LIBRARY` (default `~/.spatial-sync/library`). A missing
user root is empty. The same full scene version ID in both roots is a startup
error; different revisions of one scene name are valid.

```text
library/scenes/<name>/<rev>/
  scene.yaml       # name, title, sources, registered Python or USD stage, simulation settings
  devices.yaml     # device bindings; shared definitions remain in devices/types/
  locations.yaml   # teach locations for this scene version
  validation.json  # existing report, read without running validators at startup
```

Built-in versions are `lab-demo@1`, `lab-demo@2`, `lab-demo@3`, `falling-boxes@1`,
`robot-and-tubes@1`, `robot-and-tubes@2` and
`usd-lab@1`. Their `python:<builder-id>` stages use the existing execution
allowlist. Filesystem paths and arbitrary Python modules are not API selectors.
Newton solver and warmup settings come from the selected package; Newton itself
is imported only in its owned Runner.

`lab-demo@2` adds a virtual incubator, biosafety cabinet and environment sensor.
Version 1 retains its original bindings and layout. Version 3 keeps version 2's
behavior with pinned asset binding templates. Bare `lab-demo` launches version 3
as the latest launchable version. Synthetic device signals use the
session's optional nonnegative `seed` (HTTP, `lw scene launch --seed`, or MCP
`scene_launch(seed=...)`; gateway default 42), with
independent per-entity random streams; reset restarts them. Programs use
simulation time, first-order lag and bounded noise. An incubator configured with
an open door relaxes toward ambient temperature/CO2. `setSetpoint` and `setUv`
are high-risk actions and follow the session's existing approval policy.
Omitting seed preserves the original launch request and lets the gateway choose
its default. Close an active session before launching a new run with another
seed; same-version/source launch still reuses the active session.
New reader property metadata marks stale readings; the original scalar value
remains available. A fresh write clears expiry even when its value is equal.

Inspect and launch from any shell:

```powershell
lw scene ls --json
lw scene show robot-and-tubes --json
lw scene launch lab-demo --json
lw scene import ./my-scene --json
lw scene validate <name@rev> --json
```

`show` without `@rev` selects the latest revision. `launch` without `@rev`
selects the latest launchable revision. `--source` selects one of the package's
supported sources; omitted source uses its first source. MCP provides the same
operations as `library_search`, `library_describe` and `scene_launch`, including
when there is no active session.

`GET /api/v1/scenes` preserves `sceneId`, `name`, `sources` and description and
adds version metadata. It returns all versions. The Web app's `/scenes` page
lists every version, supports search, source and launchability filters, and
remembers the card/list view locally and in its URL. `/scenes/{name}/{rev}`
launches that exact version and opens the connected session. Sessions retains
joining by session ID. Rich discovery is
`GET /api/v1/library/scenes?q=&launchable=&source=`. Details use
`GET /api/v1/library/scenes/{name}/{rev}`; omit the revision path segment to read
the latest. Details expose declared entity bindings (including unexpanded match
selectors), device types/actions, teach locations and the existing report. They
do not claim to have inspected a Newton model's complete physical entity tree.

Session creation accepts exactly one selector:

```json
{"sceneVersion":"lab-demo@1","source":"synthetic"}
```

Legacy `{"sceneId":"lab-demo"}` remains supported, with its existing Synthetic
source default. Explicit-version requests without source use a source supported
by that version. Summaries retain the bare `sceneId` and add exact `sceneVersion`
and `sceneContentHash`. The same version and source reuse an active session;
another version requires closing it first. `sceneRevision` remains the separate
per-session manifest identity.

Unknown versions return `404 unknown_version`. Missing validation reports,
recorded failures or files changed after indexing prevent launch with
`409 scene_not_launchable`. Indexing never fabricates a missing report or runs
physics checks. Built-in reports record the package and builder checks actually
performed and explicitly warn that full structural/physics validation was not
run while packaging. Report tool-version mismatches are marked stale while the
recorded launch conclusion remains in effect.

Scene import shares the confined tar upload, immutable revision allocation and
deduplication rules of [asset import](ASSET_LIBRARY.md). New packages omit
`validation.json`; the gateway generates S1–S8 results before publication. S1
schema failures return 422 without a revision. Other failed checks remain
inspectable and prevent launch. S8 checks the selected robot's taught coordinate
counts and scalar joint limits, converting revolute degrees to radians and
retaining prismatic meters. Physics checks remain explicitly not run.
Python builders also run S5 and S8. Their registered pure scene inputs provide
the resource identities: Synthetic uses the same manifest construction as a
running session, and the native FR3 uses the builder's shared labels and pinned
URDF joint limits. The virtual arm keeps its actual coordinate count and has no
invented physical limits. Both arm programs require `approach` and `grasp` for
each taught location. Config documents, nested site/condition references and
interlocks use the same semantics as runtime Catalog construction.

USD dependencies must stay within indexed package files or pinned `library:`
asset versions. References, payloads and sublayers are preflighted before stage
composition; USDZ containers/package-member paths and unsupported composition
formats are rejected. Config location documents resolve only to indexed local
files and must select the matching robot. Imported previews and other package
files use the indexed `/library/files` route and the package integrity check.
Merged Dressing GLB export remains a later capability; reports warn when the
final GLB count was not inspected.

## Binding templates

An entity can select an exact asset version instead of repeating its type and
program. The asset's `implements` entry must uniquely match the selected source
and optional explicit `type`; its `programs[source]` selects the program.
Redundant scene `program` values must agree. Missing source implementations,
unknown versions and ambiguous entries are S5 failures that remain inspectable
and prevent launch. A malformed or unversioned template ID returns 422 before
publication.

```yaml
scene: lab-demo
entities:
- key: /World/Lab/Centrifuge
  template: device/lab-centrifuge-reference@1
  config:
    tubes: [/World/Lab/Tubes/Tube01, /World/Lab/Tubes/Tube02]
```

The loader copies template defaults, rebases only program Config fields declared
as spatial references from `/Asset` to each matched entity, then applies scene
Config overrides. An override replaces the complete top-level value, including
arrays and dictionaries. Relative spatial defaults cannot escape the asset root;
explicit scene resource overrides use the scene's actual absolute paths. Other
strings, cross-device interlocks, alerts and document names keep their existing
meaning. Config documents such as `locations.yaml` belong to the scene and require
a scene override when a template declares that field. Globs expand against actual
scene resources; zero matches and duplicate entity bindings fail.

Details retain authored declarations in `entities` and `rawBindings`. Recorded,
normalized results are in `effectiveBindings` by source; `templateResolution`
reports whether they resolved. Runtime Catalogs expose the effective bindings
and keep private alert rules private. Describing a scene does not start a source.

Reports pin every known template dependency's exact ID and raw package hash,
including when S5 fails during source or implementation selection. Launch and
revalidation reject changed dependency bytes; fixing an immutable asset requires
a new asset revision and a new scene revision. Older reports without a recorded
dependency hash cannot establish what those bytes were historically. Their first
inspection that identifies the dependency records the currently indexed bytes only.
The reader gathers pins from the immutable package report and durable validation
history bound to the same scene version and content hash. Sidecars must also match
their report ID. A failed older report or a missing latest pointer cannot erase a
previously known hash. Contradictory hashes for one exact asset ID remain an
inspectable failure; launch and revalidation return 409. Reports for another
scene/hash and different asset revisions cannot retarget an existing dependency.
Legacy hashless references establish no historical digest; malformed recorded IDs
or digests fail closed.

`lab-demo@3` and `robot-and-tubes@2` preserve previous explicit versions and
recordings. Native FR3 bindings retain the Python builder's absolute
`/World/Lab/RobotA/fr3/...` overrides over `robot/fr3@1`'s origin-relative defaults.
Native tube shape variants use separate reference assets. The new reference
assets contain extracted primitive visuals and explicit completeness gaps; they
do not replace the scenes' existing Python spatial builders or claim a physics
pass. See [Asset packages](ASSET_LIBRARY.md).

Revalidation uses `POST /api/v1/library/scenes/{name}/{rev}/validate` or generic
MCP `library_validate`. It appends a report outside the immutable version and
keeps its content hash unchanged. Existing held versions also consult the latest
report before launch/reload. USD packages can be imported and inspected; until
the USD runtime is delivered, an otherwise launchable USD version returns
`503 source_unavailable` before allocating a session.

## Static previews

Scene browsing never creates or replaces a session, starts a source, or runs
validation. An available preview is a frozen initial Manifest, Catalog, spatial
sample, property snapshot and self-contained assets from the selected package
and source. Initial readouts are static; actions require launching a session.
Physical counts exclude explicit `/World/Dressing/` nodes; logical groups and
expanded information bindings are counted separately. Missing or stale captures
show unknown counts and a reason without changing the recorded launch conclusion.

Captures live outside published packages: bundled captures in
`assets/scene-previews/`, and operator captures under
`SPATIAL_SCENE_PREVIEWS_ROOT` (default `~/.spatial-sync/cache/scene-previews`).
The reader matches the exact version, complete raw package hash and source,
then verifies every recorded producer input and indexed output file. The input
digest rule `sha256-text-lf-v1` normalizes CRLF to LF only for source/data text
(`.py`, `.json`, `.yaml`, `.yml`, `.lock`, `.toml`, `.urdf`, `.xml`, `.usda`).
Binary inputs, frozen outputs and public package content hashes use raw bytes.
Unsupported digest rules require recapture. Only indexed files are served;
external paths and unindexed model asset references are rejected.

Template captures also record `templateAssets`, the complete list of exact asset
IDs and raw package hashes used by the source. Describe and preview file reads
check these against the actual validated snapshots from the configured library
roots. External library paths stay out of capture metadata. Missing dependencies,
changed bytes, malformed or partial identity lists make that capture unavailable.
Older captures without templates remain readable; a template capture without its
dependency identities requires recapture.

Capture a producer explicitly, with no managed session or recording:

```powershell
uv run --no-sync python tools/capture_scene_preview.py lab-demo@1 --source synthetic --cache-root .scratch/scene-previews
```

For Newton, install the simulation and USD extras before starting any process
that uses the environment, then run the same tool with `--source newton` and a
Newton version such as `robot-and-tubes@1`. Captures run on CPU. A tool-only
spawn wrapper observes the unchanged production Runner's executed project code
and data inputs. URDF dependencies come from the actual importer/resolver; USD
dependencies come from the composed Stage's used layers and native dependency
resolution. Unresolved or repository-external native inputs reject the capture
with a reason. Python file auditing alone does not cover native file reads.

The output root must be outside every published scene package, and an existing
capture directory is never overwritten. The Web list generates cached PNGs
through one shared offscreen viewer; cards contain images. The detail route
reuses the same renderer and owns one viewport. A related operational recording
is never substituted for an initial scene preview; its recorded content hash is
shown as matching, different or unknown.

Published version files are immutable. Place edits in a new revision, preserve
its validation report and restart the gateway to discover manually published
packages. Content hashes include all package files in sorted relative-path
order. Teach-location tools read the built-in package path; capture new teaching
output into a new version rather than overwriting an existing one.

New recordings use metadata format 2 and retain the session's exact
`sceneVersion` and `sceneContentHash`. Scene details list recordings attributed
to that exact version. Existing format 1 recordings continue to replay; readers
derive `sceneId@1` and an unknown content hash without rewriting the original
file. Live format 2 recordings retain null scene identity and are not attributed
to a scene version. See [Recording compatibility](RECORDINGS.md).
