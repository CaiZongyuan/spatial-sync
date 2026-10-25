# Build a scene with an external Scene Director

Lab World provides catalog search, bounded acquisition, immutable imports,
validation and exact-version launch. Requirements and layout confirmation stay
in the person's original Agent conversation. Blender and the checkout's
`factory/` directory produce the packages outside the gateway.

## Start an owned workspace

Use Python 3.13, the frozen locks and the optional simulation/USD dependencies.
Blender authoring uses the version and configuration in
[`factory/README.md`](../factory/README.md). Catalog and library operations do
not need Blender. The installed wheel includes the Scene Director skill; the
factory scripts additionally require a repository checkout.

```powershell
uv sync --frozen --group dev --extra simulation --extra usd
pnpm install --frozen-lockfile
$env:LW_LIBRARY = "$PWD/.scratch/scene-director/library"
$env:SPATIAL_STATE_ROOT = "$PWD/.scratch/scene-director/state"
$env:SPATIAL_RECORDINGS_ROOT = "$PWD/.scratch/scene-director/recordings"
$env:LW_URL = "http://127.0.0.1:8000"
uv run --no-sync python -m uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000
```

Choose unused ports when another task owns the defaults. In another terminal,
start `pnpm dev`; set `SPATIAL_API_URL` when the API uses a different port.
Pass `LW_URL` to the Agent's CLI and MCP environment. An MCP subprocess does
not necessarily inherit all environment variables from its client: supply
`LW_URL` explicitly in its stdio server configuration. Health is
`GET /api/v1/health`.

List or install the shipped instructions:

```powershell
uv run --no-sync lw skills ls --json
uv run --no-sync lw skills install --target .scratch/scene-director/agent-skills
```

Give the external runtime the installed `lab-scene-director/SKILL.md` and the
official `lw mcp` stdio server, or the `lw` CLI. Use the
[Scene Director skill](../skills/lab-scene-director/SKILL.md) for its ordered
workflow, a valid Authoring Report input and the MCP tool names. A paid runtime
run needs the model/version, credentials reference, token/cost ceilings, time
window, allowed tools and cleanup authorized in its issue. Record reported
usage separately from independently verified provider billing.

## Complete the authoring loop

1. Search both the local library and the online catalog (the curated offline
   seed is used until a full directory sync is published). Read exact
   candidate identities, source revisions, unknowns, license admission, usage
   and acquisition eligibility. Estimate before acquiring; bind the exact local
   version returned by the completed acquisition.
2. Describe a dimensioned graybox, its assumptions and gaps in the original
   conversation. Continue after the person confirms that layout. A missing
   trusted robot is a gap to resolve with the person.
3. Use the plans in `factory/plans/` and the `authoring_request` / `build` API
   documented in `factory/README.md`. Generate a scripted Blender workspace or
   extract the layout from a saved human edit. Keep `.blend` files, requests and
   provenance outside the imported library. OpenUSD composition keeps original
   trusted Asset references; a Blender visual round trip is not physics proof.
4. Import generated Assets, then compose/import the Scene using their returned
   exact versions. The gateway creates validation reports. Read actual failed
   checks and measurements, repair the external inputs and import a new version.
   Keep the previous failure and its report readable.
5. Launch a passing exact Scene Version on Newton CPU, inspect the real platform
   viewport, and close the owned Session before starting another. Reuse the
   repaired Asset in a second Scene and find it through local catalog search.

For the collision-repair example, `tube-rack-demo-v1.yaml` has visual holes with
a solid collision box; `tube-rack-demo-v2.yaml` keeps the dimensions and repairs
the collision around the holes. A rendered hole is not a passing seating test.
`tube-rack-reuse.yaml` demonstrates reuse of the repaired Asset.

An external Authoring Report states what its source says it did. Its receipt is
distinct from the platform's validation. Use `object` with the returned exact
ID and content hash to bind a report to an imported version. Artifact references
are opaque text; the gateway does not open their files.

## Verify version isolation

Record an imported Scene's hash and object pose, then change only the external
Blender workfile by +0.20 m. Before export/import, the platform must retain the
original version, hash and pose (relative displacement 0.00 m). Export the
changed layout and matching plan, import a new version, and verify that version
has the +0.20 m displacement while the old version is unchanged. A saved workfile
does not update the platform implicitly. Validate the new version independently.

The real-tool acceptance includes the original confirmation, tool trace,
acquisition result, Blender input/layout hashes, immutable versions, applicable
S/P reports, viewport and Newton Session evidence, failure repair, second-scene
reuse, and correct exits for unavailable candidates or budget restrictions.
Keep raw captures under owned `.scratch/` directories and post conclusions in
the delivery issue. Distinguish scripted edits from human edits and reused
evidence from newly executed work.

Run the applicable checks in [VERIFICATION.md](VERIFICATION.md). CI uses small
synthetic packages and a labeled semantic stand-in; it proves interface and
failure behavior. Real semantic quality, source adaptation, Blender, Newton and
Agent acceptance have separate evidence. This workflow does not establish
robot manipulation or training success.
