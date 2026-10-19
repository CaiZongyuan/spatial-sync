# Scene versions

The gateway discovers scene packages at startup from the repository's read-only
`library/` and from `LW_LIBRARY` (default `~/.spatial-sync/library`). A missing
user root is empty. The same full scene version ID in both roots is a startup
error; different revisions of one scene name are valid.

```text
library/scenes/<name>/<rev>/
  scene.yaml       # name, title, sources, registered Python stage, simulation settings
  devices.yaml     # device bindings; shared definitions remain in devices/types/
  locations.yaml   # teach locations for this scene version
  validation.json  # existing report, read without running validators at startup
```

Built-in versions are `lab-demo@1`, `falling-boxes@1`, `robot-and-tubes@1` and
`usd-lab@1`. Their `python:<builder-id>` stages use the existing execution
allowlist. Filesystem paths and arbitrary Python modules are not API selectors.
Newton solver and warmup settings come from the selected package; Newton itself
is imported only in its owned Runner.

Inspect and launch from any shell:

```powershell
lw scene ls --json
lw scene show robot-and-tubes --json
lw scene launch lab-demo --json
```

`show` without `@rev` selects the latest revision. `launch` without `@rev`
selects the latest launchable revision. `--source` selects one of the package's
supported sources; omitted source uses its first source. MCP provides the same
operations as `library_search`, `library_describe` and `scene_launch`, including
when there is no active session.

`GET /api/v1/scenes` preserves `sceneId`, `name`, `sources` and description and
adds version metadata. It returns all versions. The temporary Sessions launch
form projects one latest launchable choice per bare name. Rich discovery is
`GET /api/v1/library/scenes?q=&launchable=`. Details use
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
recorded launch conclusion remains in effect. The full importer/validator is
separate work.

Published version files are immutable. Place edits in a new revision, preserve
its validation report and restart the gateway to discover manually published
packages. Content hashes include all package files in sorted relative-path
order. Teach-location tools read the built-in package path; capture new teaching
output into a new version rather than overwriting an existing one.

Recording format 2 is a separate change. Existing format 1 recordings continue
to replay. Version-attributed related recordings are currently unknown in scene
details; the API does not invent a version for recordings lacking that metadata.
