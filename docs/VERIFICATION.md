# Launch and verification

Desktop web is the approved scope. Use one checkout's source and frozen locks.
Only one session can be active; tests must not share a manual session.

## Launch

Follow the root README. Health is `GET /api/v1/health`. Use Synthetic Laboratory
for GPU-free checks. Newton requires `--extra simulation --extra usd`.
Do not resynchronize an environment while it serves another process;
`uv run --no-sync` preserves installed extras. Use one API worker and one Runner.

## Mandatory checks

```powershell
uv run --no-sync python -m pytest tests/contract tests/unit tests/gateway tests/cli -q
uv run --no-sync ruff check apps packages tools tests factory
pnpm protocol:check
pnpm test:protocol:ts
pnpm test:web
pnpm build
pnpm --filter @spatial-sync/web exec playwright install chromium
pnpm test:e2e
```

CI keeps two logical Python lanes: contract/unit/CLI core and gateway. Core has
one execution shard; gateway has two. Each still collects its whole directory
set before selecting `sha256(nodeid UTF-8) % count`, then uses `-n auto` within
its own runner. `SPATIAL_CI_PYTEST_INDEX` and `SPATIAL_CI_PYTEST_COUNT` are paired,
one-based settings used only by CI; with neither, ordinary local tests retain the complete
collection. Invalid or incomplete settings fail instead of silently selecting
a subset. `SPATIAL_CI_PYTEST_MANIFEST_DIR` records each worker's full, selected and
deselected identities; deselected nodes are not skips.

Core also collects all four mandatory directories independently. The `contracts`
job downloads all three attempt-specific Python artifacts and runs
`python tools/check_ci_python_coverage.py <artifact-directory>` to verify worker
agreement, the exact disjoint selection union and all three JUnit identity sets.
It derives totals from the full collection. The workflow has 17 checks: three
Python executions, twelve browser shards and two aggregate checks.

Start an owned `approval_gateway:app --app-dir tests/fixtures` API first. E2E
defaults to API8000 and isolated web5184. `SPATIAL_API_URL` selects another API;
`SPATIAL_WEB_URL` selects an existing web server. Both must serve the candidate
checkout. Include custom browser origins in `SPATIAL_ALLOWED_ORIGINS`. CI installs
base Python dependencies and Chromium. The gateway declares `closeLid` high-risk
in the test Catalog before the real Synthetic host starts; the approval journey
also needs `SPATIAL_TEST_APPROVAL=1`. Production device definitions are unchanged.
`LW_LIBRARY`, `SPATIAL_STATE_ROOT` and `SPATIAL_RECORDINGS_ROOT` must be owned
directories under `.scratch/`; unset, the gateway uses `.scratch/viewer-e2e`.
E2E refuses an API that does not report such roots.
Browser CI builds the Web app in each shard and sets `SPATIAL_E2E_WEB_MODE=preview`
to serve that checkout's `dist` through Vite preview. Its API, WebSocket and
recording proxies use the same configuration as the development server; the Live
test's separately owned web process uses the same mode with its own API URL.
The `live-operations.spec.ts` case owns a `live_operations_gateway` and its roots;
it exercises the real DeviceHost, programs, recording and HTTP pipeline with
socket-free in-process device peers. It asserts no Session or spatial Source is
created. This case is separate from `live.spec.ts`, which retains the Synthetic
session telemetry journey. Actual broker wire behavior uses the local broker and
mock-device launch in `docs/LIVE_DEVICES.md`.
Local E2E keeps Vite dev by default. After `pnpm build`, set this flag to exercise
the built app locally; `SPATIAL_WEB_URL` still selects an existing owned server.
CI leaves routine test video off. `SPATIAL_E2E_VIDEO=1` opts in locally; failure
traces/screenshots and explicit captures remain enabled in either mode.
CLI tests use the installed `lw` entrypoint, actual HTTP, official SDK clients
and stdio; run `uv sync --frozen --group dev` before the environment serves any
other consumer. Each test and benchmark owns its recording directory.
Set `SPATIAL_STATE_ROOT` to the same isolated directory for the owned API and E2E
process (for example `.scratch/viewer-e2e/state`). The model-page journey seeds
only a generated fake credential locally, never through HTTP or in the user's Vault.

## Information and recording checks

### Existing runtime records

The GPU-free import journey uses the actual Claude Code stream-format fixture
and its provenance in `tests/fixtures/runtime/`. It needs no runtime process,
authentication, model registry or provider request:

```powershell
uv run --no-sync python -m pytest tests/gateway/test_runtime_records.py tests/gateway/test_runtime_native_edges.py tests/cli/test_runtime_commands.py -q
uv run --no-sync python tools/check_platform_skills.py
lw runtime import tests/fixtures/runtime/claude-code-stream.jsonl --format claude-code-stream-json --json
lw runtime ls --json
lw runtime show <runtime-session-id> --json
```

Use owned state roots for the running API. Reimport the selected file and confirm
idempotence, then open `/runtime-sessions` and inspect the returned session's
messages, usage and transcript/usage evidence. Missing request and total-token
counts stay unknown; the fixture's native capture cost is retained once. The
cost dashboard buckets it by first import observation, not execution time.
CLI/MCP tests use the installed entrypoint, real HTTP and official MCP client.
The runtime browser journey owns an API that refuses all spatial Source creation.
It covers a growing capture, retry, public evidence and cost readback; importing
text does not create evaluations, approvals or jobs. These checks are separate
from the optional model-driven I5 journey below. See
[runtime records](AGENT_HARNESS.md#import-existing-runtime-records) for supported
formats, optional associations and conflict behavior.

### External Agent evaluation handoff

The CPU-only B checks use a scripted external controller over real HTTP and the
same installed CLI/official MCP tools. They create real Synthetic Sessions;
they execute no model or SDK:

```powershell
uv run --no-sync python -m pytest tests/gateway/test_external_agent_evaluation.py tests/gateway/test_external_agent_lifecycle.py tests/gateway/test_external_agent_restart.py tests/gateway/test_external_agent_suite.py tests/gateway/test_external_agent_evidence.py tests/gateway/test_external_agent_native_links.py tests/cli/test_agent_run_commands.py -q
```

The public Suite journey completes ten tasks × three unique recordings and
publishes only `reported` scores. The one-case path excludes complete results.
Paired physical-evidence checks distinguish missing/stale/wrong scope from
fresh known-false outcomes; private rubric/thresholds remain unchanged. HTTP
state reads expose `X-Spatial-Scene-Revision` and `X-Spatial-Epoch` from the
actual information store, allowing final evidence to verify its scope without
changing the existing JSON or recording format.

The restart check kills only its own fixture gateway through its creating
Popen handle, restarts on the same owned roots and proves old grants/completion
cannot revive effects. The Source runs in that owned process; no external
runtime is started or killed. Cancel/expiry and controlled Source cleanup
failures also prove the shared slot remains blocked until confirmation.
`tests/e2e/agent-runs.spec.ts` owns its API/Web and separately labels actual
one-case evidence and controlled complete/invalid UI fixtures.

Use the existing authenticated remote/operator-token mode for the later real
runtime single-case smoke. Controller credentials stay outside Target; the
Target receives only the case grant and scoped MCP. Runtime context/general
tool configuration remains external. Real model execution needs a concrete
model, budget, time limit and authorization after implementation/review.

### Agent RSI

The Agent training page is `/train?layer=agent&agent=agent/lab-operator&goal=goal/name`.
It reads measured Reference/candidate versions, a 0–100 mean/range chart, rejected
versions, exact Harness composition, public external runs, Goal budget and Lab
Notebook. Agent Plans use `kind: training, layer: agent`; existing Plans without
a layer remain embodied. The shared Plan and approval pages render both kinds.

`lab-eval-build` publishes an explicitly selected Suite and establishes a measured
Reference. `lab-harness-optimize` stages one change, submits a Plan and settles
its approved evaluation. `lw mcp` selects the restricted Optimizer or separate
promotion-controller tools from the issued token; HTTP enforces the same scope.
Controller credentials stay outside Optimizer and Target.

```powershell
uv run --no-sync python -m pytest tests/unit/test_agent_transitions.py tests/gateway/test_agent_rsi.py tests/gateway/test_agent_promotions.py tests/gateway/test_agent_contamination.py tests/gateway/test_agent_rsi_recovery.py tests/gateway/test_agent_optimizer_scope.py tests/cli/test_agent_rsi_commands.py -q
uv run --no-sync python tools/check_platform_skills.py
pnpm --filter @spatial-sync/web exec playwright test agent-rsi.spec.ts
```

The complete baseline, accepted/rejected, promotion, budget and installed-client
journeys use actual Synthetic grades and the production identity producer.
Namespace and isolation fixtures explicitly substitute comparison identities;
they do not substitute Source observations or grades. Publication-failure probes
retain the failed receipt and verify actual files plus durable restart recovery.
The browser case owns its API/Web and exercises real train/private repeats,
human promotion, rejection restoration, contamination, native unknowns, human
Notebook audit, network retry and layer switching. Run it with
`SPATIAL_E2E_WEB_MODE=preview` after a build to verify the built app.

Held-out work uses the same queue. An opaque pending item must bind a fresh
native conversation before the private statement/Target token is released.
Ordinary B/A, recording/history, Notebook, cost and approval/inbox projections
hide private details while actual private usage stays in aggregate costs. Late
private metering remains importable through the owning controller intake even
after contamination stops work. Known Optimizer native conversations cannot be
reused as Targets. A denied read alone does not establish exposure.

These scripted controllers run no model, GPU training or physical device. Actual
runtime/model execution remains a separately authorized bounded smoke; the
platform's interface grants do not attest an external runtime's other files,
tools, SDK or networks. Observed native reports stop future admissions; they do
not provide an external pre-call hard USD guarantee.

### Training Goal and Lab Notebook

The policy engineering journey uses actual Newton CPU/PhysicsPort, a small
finite ONNX model and the existing HTTP evaluator/recording. It compares a
measured teacher with two candidates whose training Task hashes differ while
their fixed Suite comparison group remains the same. The deliberately
insufficient candidate succeeds at its own gripper goal and correctly scores0
on the Suite's tube-transfer goal. This is engineering evidence, not a learned
contact-policy success or the actual256 trainer-state acceptance.

```powershell
uv run --no-sync python -m pytest tests/gateway/test_policy_observation_evidence.py tests/cli/test_policy_observation_commands.py -q
uv run --no-sync python -m pytest tests/sim_runtime/test_policy_evaluation_api.py -q
```

The second command needs the frozen simulation/USD extras and owns its
temporary inputs/Session/recording. Source/artifact checks retain unqualified
fixture labels. See [policy candidates](POLICIES.md) for actual subject and
observation-evidence commands.

Controller promotion adds transaction/privacy checks and an actual Newton CPU
private-test journey:

```powershell
uv run --no-sync python -m pytest tests/gateway/test_controller_promotions.py tests/gateway/test_controller_promotion_privacy.py tests/gateway/test_native_observation_evidence.py tests/unit/test_controller_promotion_admission.py tests/unit/test_controller_publication.py tests/unit/test_deployment_authority.py -q
```

The Newton journey is included in `test_policy_evaluation_api.py` above. It uses
real Sources/actions/grades/recordings while explicitly substituting only the
unmet trainer qualification gate. Transaction tests separately mark controlled
scores/qualification and physical validation. They prove human approval,
interruption/restart recovery, concurrent current/Reference fencing, budget and
privacy behavior; they do not certify actual native trainer256/contact quality.

Evaluation comparison groups require complete server evidence for the fixed
environment and judge. Candidate versions remain separate. Legacy scores and
decisions stay readable; missing comparison evidence is unknown and cannot
establish a new Reference. Notebook reads expose historical comparisons as
`recordedComparison` when current evidence differs, preserving the original
automatic result and human audit. Private recoverable inputs live under the
owned state's `evaluations/identity-inputs/`, outside public API projections.
Snapshots include the platform's actual library resolvers, HTTP action/control
boundary and `lw` tool/schema/client implementation. External runtime/client
SDK code and context remain unattested, with declaration provenance retained.

Healthy task deadlines retain valid failed episodes and continue the fixed
protocol. Invalid observations or Source failures retain partial attempts,
actual recording links and cleanup evidence, with unknown success and no
complete score. The Evaluation page's detail selector includes these attempts.

```powershell
uv run --no-sync python -m pytest tests/gateway/test_evaluation_identity_restart.py tests/gateway/test_evaluation_deadlines.py tests/gateway/test_notebooks_api.py -q
```

An approved Training Plan's linked train Evaluations produce public Notebook
records, including failed and cancelled attempts. Use the Plan's returned
`goalId` (by default `goal/<Task.name>`, stable across Task revisions):

```powershell
lw eval run robot_arm.virtual --suite suite/lab-demo-pick-place@1 --plan plan-0007 --revision 1 --json
lw notebook show goal/fr3-pick-tube --json
lw notebook show goal/fr3-pick-tube --refuted --json
lw loop show goal/fr3-pick-tube --json
```

Replace the example Plan, Goal, subject and Suite with matching fixed inputs.
The CLI and generic MCP tools `notebook_show` / `loop_show` read the same HTTP
resources. The Training page selects a Goal with `?goal=goal%2F<name>` and shows
scores, Wilson intervals, comparison groups, hypotheses and cumulative budget.
An operator can add a reasoned human judgement there; the automatic result,
Candidate acceptance and deployment decision remain separate.

The canonical state is `SPATIAL_STATE_ROOT/notebook-records.json`; retryable
Markdown/YAML projections live under `LW_LIBRARY/notebooks/<goal-name>/`.
Private/test materials are excluded. Unknown legacy limits, execution identities
and LLM cost remain unknown. Plan revisions share one budget pool; the first
durable human approval fixes the Goal limit and wall-clock anchor.

`tests/cli/test_notebook_commands.py` exercises the installed entrypoint and
official MCP stdio client. `tests/e2e/notebooks.spec.ts`, with
`SPATIAL_E2E_WEB_MODE=preview` after a build, owns a separate API/Web process and
verifies two controlled evaluator rounds, CI lines, budget exhaustion and durable
human audit. Its labelled fake evaluator adapter runs no physics or training;
the gateway Notebook tests separately exercise actual Synthetic evaluation.

### Demonstrations and pinned training inputs

Use an actual successful episode recording and its exact source Task. A linked
embodied train Evaluation supplies the frozen Task; ordinary session recordings
require it explicitly:

```powershell
lw demo add session-EXAMPLE --task task/fr3-pick-tube@1 --json
lw demo ls --task task/fr3-pick-tube@1 --json
```

The generic MCP tools are `demo_add` and `demo_list`. Copy the returned immutable
`set.id` into the consuming Task's `demonstrations` list with `purpose: reset` or
`purpose: imitation`, push that Task version, then submit its Plan normally.
The Task author consumes node-local paths from `LW_DEMONSTRATION_SETS`; this
does not supply an imitation-learning or reset implementation.

Frozen recording packages live under
`LW_LIBRARY/demonstrations/recordings/<recordingId>/`; cumulative immutable indexes
live under `LW_LIBRARY/demonstrations/sets/<name>/<revision>/manifest.json`.
The returned `recordingUrl` can be passed as the existing replay page's `src`
query. Recording metadata, binary segments and assets retain their original
bytes. The dataset remains available after ordinary recording retention.

The gateway journey `tests/gateway/test_demonstrations_api.py` creates two actual
Synthetic successes, pins a Task to DemoSet v1 before v2 exists, then runs the
real isolated node with the explicitly fake CPU trainer. It checks downloaded
hashes, local paths, `versions.json` attribution and rejection of missing dataset
references. `tests/cli/test_demonstration_commands.py` uses installed `lw`, actual
HTTP and official MCP stdio. These prove data transfer; they do not train a policy.

Admission rejects failed/unknown outcomes, gaps, mixed episode identities and
known held-out sources. Agent case scores are unsupported as physical
Demonstrations. Legacy recording replay remains supported; missing historical
program implementation identity stays unknown and missing scene identity cannot
be inferred from current files.

The latency budgets in information specification section12 use this development
machine's Linux/WSL2 environment, as used for the I3 acceptance, as the reference.
Windows results are diagnostic references and do not gate delivery. Before
optimizing a performance issue, compare the candidate and acceptance baseline
under the same environment, workload and measurement boundary; use
`check-benchmark` to assess the comparison. #70 tracks the Windows difference
and does not block I5 or another ticket. Record the I5 MCP journey on Linux.

Run the I5 runner on Linux/WSL2 with the frozen simulation/USD extras, installed
web dependencies and Playwright Chromium, and an authenticated Claude Code CLI
on `PATH`. Its process-group cleanup requires POSIX. The runner is one command.
It starts its own Newton-capable API and web
server on fresh roots under a timestamped output directory, then gives Claude
Code an empty working directory, no built-in tools and only the `lab` MCP server:

```bash
uv run --no-sync python tools/agent_journey.py
```

The agent launches `robot-and-tubes` with Newton itself. While it works, every
settled tube Site location is checked against physics whenever the rotor is at
rest, and a browser observer compares the viewer's locations and resting poses
with the API. The run fails unless the invocation log completes the I3 order
with `agent:claude-code` only and the recording completes. The output holds
`receipt.json`, the transcript, checkpoint screenshots and the zipped recording;
publish the recording and transcript as release assets, never in the repository.
Each tube must have a finite resting-pose comparison; a missing or non-finite
rendered pose persisting for two samples fails. Sample rounds wait 400 ms plus
HTTP, browser and screenshot work; they do not prove frame-by-frame agreement.
Cleanup failures retain owned process identities in the failure receipt and do
not stop cleanup attempts for other owned groups.

The browser suite includes real format1 and format2 recording fixtures. Format1
retains ten pose/location/joint samples; format2 pins a selected scene version
and compares the final paused state with independent public HTTP reads. Live
device actions and replay share the same stores
and reducers. Replay must make no live session requests or WebSocket connection,
and its action, approval, cancellation and lifecycle controls must be disabled.

```powershell
uv run --no-sync python tools/bench_info.py --entities 1000 --rate-hz 15 --generation-hz 15 --http-hz 10 --seconds 12 --repeats 3
uv run --no-sync python tools/bench_info.py --entities 1000 --rate-hz 15 --generation-hz 15 --http-hz 10 --seconds 12 --repeats 3 --include-newton
uv run --no-sync python tools/bench_recording.py
```

Run performance commands with an exclusive measurement window. Inspect actual
changes/s, completed HTTP rate, both readers' exact counts/digests, transform
cadence and every budget in specification section12. A `measured` receipt only
means measurements completed; it does not declare the latency budgets passed.
`--generation-hz` controls real property writes and defaults to `--rate-hz`,
which independently limits publication. Inspect generated writes, Source
publication and each reader's actual received rows separately. Every loaded
measurement window must deliver at least10,000 changes/s to both readers.
The earlier publication-only stress workload is reproducible with
`--rate-hz 20 --generation-hz 60`; its results are a different workload and
cannot establish a before/after speedup against15Hz generation.
The log benchmark measures admission through actual drain, buffered flush and
metadata close, validates every written frame, and reports queue depth and loop
delay separately. It does not measure fsync durability. Keep benchmark output
outside Git and summarize final results in the PR description.
With the simulation extra installed, `--include-newton` adds a separate CPU FR3
phase after the Synthetic gateway closes. It uses real alternating gripper actions,
two WebSocket readers, registered waits and Runner control timing. The final log
phase measures real output after both gateways close. A default run without this
option explicitly marks Native timing as unavailable; it does not estimate it.

For crash recovery, force-stop only an owned gateway after a session shorter
than60s has written samples, restart against the same recording root and verify
`incomplete`, recovered duration and browser playback. Also cover an empty or
partial first header: it must become incomplete without inventing samples.

## Real physics and browser checks

```powershell
uv run --no-sync python tools/smoke_newton.py --device cpu
uv run --no-sync python tools/smoke_newton_source.py --scene robot-and-tubes --device cpu --crash
uv run --no-sync python tools/smoke_info_newton.py
uv run --no-sync python -m pytest tests/sim_runtime -m "not gpu" -q
```

These require installed simulation/USD extras and use the actual Newton engine.
All four commands above are required before merging changes to
`packages/sim_runtime/**`, `packages/programs/**`, `library/scenes/**`, or a
recaptured Newton preview under `assets/scene-previews/**`. Run the commands
against the candidate with owned state/library/recording roots, retain their
results, and add a `Newton CPU` paragraph with commands, results and tick P95
to the PR. Use Linux/WSL2 for the latency gate; keep Windows observations
separate. A focused native test is additional evidence, not a replacement for
these four checks.
Optional browser journeys use `SPATIAL_NEWTON_E2E=1` and `SPATIAL_TEST_NEWTON=1`.
Failure probes can terminate only the Runner belonging to their own session.

Static previews verify the actual source files used for their capture. After an
input changes, regenerate the affected version/source, for example:
`uv run --no-sync python tools/capture_scene_preview.py robot-and-tubes@2 --source newton --cache-root .scratch/scene-previews`.
Copy the generated capture directory into `assets/scene-previews/`; keep published
scene and asset package bytes unchanged.

Launch each registered scene/source, select Body and Site, Pause, Step, Reset
and Resume. Two browsers must share the authoritative sample and control results.
Compare paused Inspector and HTTP pose at matching seq/simTime; Body and Site
origins can differ. Close your session before changing scenes. Reload changes
sceneRevision. Quantized JSON poses allow documented rounding tolerance.
GPU submission timing must not be reported as GPU execution time.

## Platform operations and native viewer

`GET /api/v1/platform` reads a background cache of managed sessions and gateway
host resources. The GPU-free gateway tests inject a sampler; they do not prove
actual driver telemetry. Verify real CPU/RAM/NVIDIA values separately on a host
with `nvidia-smi`. Unknown values remain null with a reason. An initial CPU
sample needs a second measurement before a percentage is meaningful.

```powershell
uv run --no-sync python -m pytest tests/unit/test_platform_monitor.py tests/gateway/test_platform.py tests/unit/test_native_viewer.py -q
uv run --no-sync python -m pytest tests/sim_runtime/test_native_viewer.py -q
$env:SPATIAL_PLATFORM_NEWTON_E2E = '1'
pnpm --filter @spatial-sync/web exec playwright test platform.spec.ts --grep 'real native'
```

The last two checks need the frozen simulation extras and an owned API. The
browser case opens the actual robot scene's native viewer through the platform
button, checks its Runner identity and repeated URL, reloads the session, opens
the replacement viewer, then stops it and checks backend counts. Ordinary CI
explicitly skips this optional native case. Other operations browser cases
separate contract mocks from the real Synthetic connection/pause/stop journey.

## Scene Factory support baseline (SF0)

The gateway never imports `factory/`. Its GPU-free check uses a self-authored
SimReady-shaped source, the restricted adapter and the existing immutable import:

```powershell
uv run --no-sync python factory/verify.py check
```

The same journey runs as `tests/unit/test_scene_factory.py` in the core lane. It
uses only structural checks, so it stays GPU-free and independent of the
simulation extras. The committed boundary is `factory/support.json`; `check`
validates it for pinned identity, verified BOMs, measured outputs and honest
usage flags. Real Newton evidence is recorded by the separate evidence run.

Real source and Blender evidence is separate from CI and needs network access.
The factory evidence run also needs the simulation/USD extras and Pillow for the
Newton CPU usage decision and texture handling; use `uv run --with pillow --no-sync`
or a local environment that has them without changing `uv.lock` (committed scene
previews hash `uv.lock`):

```powershell
uv run --no-sync python factory/verify.py evidence --cache .scratch/sf0/cache --out .scratch/sf0/evidence
uv run --no-sync python factory/verify.py blender --blender <blender.exe> --mcp <mcp-for-blender.exe> --out .scratch/sf0/evidence/blender.json
```

The pinned combination is Blender 4.5.9 LTS, MCP for Blender 2.2.2 at commit
`5a3618fccb2d982d0dd61e7a1879912c3100073b`, addon `bl_info` 1.8,
`BLENDER_MCP_SAFE_MODE=1`, `DISABLE_TELEMETRY=true`, no auto-started socket and
all third-party providers disabled. The Blender probe runs a background script;
the MCP probe uses the official client to check safe-mode rejection. The 2.2.2
wheel omits `blender_mcp.config`, so telemetry is recorded as unavailable rather
than verified; the addon socket is only for authoring and is not a sandbox.
Raw downloads and probe output live in `.scratch/sf0/`; no NVIDIA asset, model
weight or Blender binary enters Git.

The SF implementation tickets record their supported external agent client and
any model/cost authorization in the relevant issue. Real agent calls are not part
of ordinary CI. Factory acceptance separately checks actual Blender authoring,
received-version isolation, real Newton CPU validation and offline use.

## Scene authoring factory (SF3)

The pure scene plan, procedural geometry, OpenUSD composition and layout
receipts are GPU-free and run in the core lane:

```powershell
uv run --no-sync python -m pytest tests/unit/test_scene_authoring.py -q
```

`factory/plans/` contains the reproducible plans. A build returns the canonical
asset and scene packages; Blender is only a layout workspace and never writes
USD. The background authoring entry records the scripted or human-edited path:

```powershell
<blender.exe> --background --factory-startup --python factory/blender_author.py -- `
  --request <request.json> --blend <workspace.blend> --layout <layout.json>
<blender.exe> --background --python factory/blender_author.py -- `
  --request <request.json> --open-blend <edited.blend> --layout <layout.json>
```

Real evidence uses an installed trusted asset library, imports the generated
packages through the normal gateway, and validates/launches the exact scene
version. It is separate from CI: the gateway never imports `factory/`, and the
committed tests use no Blender, network or GPU. Record the Blender version, plan
and request hashes, layout mode, immutable content hashes, S/P checks, viewport
capture and any remaining failure in `.scratch/`; an image or a redrawn report
is not a physics result.

## Scene Factory catalog (SF1a)

The committed snapshot is `catalog/simready.json`; search needs no network, cache
or Blender. Installed `lw` and `lw mcp` (`catalog_search`, `catalog_show`,
`catalog_estimate`) read the same HTTP routes. Source sizes are bytes and
dimensions are metres; usage is `dressing|staticCollision`, license is
`admitted|not_admitted`, limit is 1..50 and unknown attributes never satisfy a
hard filter. Use a returned `nextCursor` to continue, and pass a summary
`documentDigest` to detect a changed entry:

```powershell
lw catalog search -q 纸箱 --scope all --json
lw catalog search -q carton --usage dressing --license admitted --limit 10 --json
lw catalog show simready/box_cardboard_a01@524747d9b5e1f21b5b18b56041788c01d745f1a1 --view full --json
lw catalog estimate simready/box_cardboard_a01@524747d9b5e1f21b5b18b56041788c01d745f1a1 --json
```

`tests/cli/test_catalog_commands.py` compares HTTP, the installed entrypoint and
official MCP stdio for bilingual search, filters, pages, errors and the retained
local `library_describe` detail shape. The GPU-free gateway tests cover snapshot
loading, correlation, bounds and estimate limits. The browser journey uses the
same owned fixture gateway as the asset library:

```powershell
pnpm --filter @spatial-sync/web exec playwright test catalog-search.spec.ts
```

## Scene Factory semantic recall (SF1b)

Natural-language use queries may add CPU semantic recall when keyword matching
finds no online candidate. Recall is only a soft recall/ranking layer: exact
identities, scope, hard filters, cursors, snapshots, the 32 KiB response budget
and unknown-attribute semantics are unchanged. Every response carries
`semantic.model`, `semantic.index` and the existing `snapshot` versions.

CI uses the deterministic, offline `lw-hash-concept-standin`; it is labelled
`deterministic-stand-in` with `real: false` and is not real-model evidence. The
Web snapshot bar, CLI and MCP show the same HTTP document. A real CPU model can
be plugged in for separate evidence; its weights are never committed:

```powershell
lw catalog search -q "用来装实验室废弃物的容器" --scope online --json
lw catalog search -q "a container for storing lab supplies" --scope online --json
uv run --no-sync python tools/eval_catalog_semantic.py --output .scratch/sf1b/eval-standin.json
uv run --no-sync python tools/eval_catalog_semantic.py --benchmark-records 1000 --output .scratch/sf1b/eval-standin-1000.json
```

The versioned corpus is `tests/fixtures/catalog/semantic/catalog.json` (5 real
SimReady entries plus 100 synthetic bilingual fixtures) and `queries.json`
(40 queries, 20 per language, at least eight retained per language, with
snapshot ID and initial Agent relevance labels). Synthetic source, license,
size, adaptation and usage fields are fixtures, not source facts. The label
file flags conflicts for user spot-check and adjudication; user adjudication and
Linux/WSL 1000-record P95 remain user-gated and are not claimed by this check.
Real-model evidence uses a standalone environment, for example:

```powershell
uv run --with fastembed --no-sync python tools/eval_catalog_semantic.py `
  --embedder fastembed --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 `
  --output .scratch/sf1b/eval-real.json
```

This downloads a model only for that evidence run; no third-party weights or
bytes enter Git and no paid or remote inference is used. The deterministic
tests are `tests/unit/test_catalog_semantic.py`, the gateway semantic case in
`tests/gateway/test_catalog_search.py`, and the shared HTTP/CLI/MCP journey in
`tests/cli/test_catalog_commands.py`.

## Scene Factory acquisition worker (SF2)

The gateway never imports `factory/`; the acquisition runner starts
`factory.acquisition_worker` as a subprocess with a server-created job. The
GPU-free worker test uses the synthetic fixture source, so it needs no network:

```powershell
uv run --no-sync pytest tests/unit/test_acquisition_worker.py -q
```

It exercises the staged `asset.yaml`/`LICENSE.txt`/`SOURCE-NOTICE` package,
Git LFS pointer rejection (`lfs_pointer`), BOM and unsafe-path failures, the
source/adapted byte limits, and the progress/result JSON. Failures leave no
staged package.

Real box and bucket evidence needs network access and is not a CI prerequisite.
The job files are server-created (never a user URL or path); keep the cache,
staging and result under `.scratch/` so no third-party bytes enter Git:

```powershell
uv run --no-sync python -m factory.acquisition_worker --job <server-created job.json>
```

The worker downloads the pinned commit, verifies file/byte counts, BOM closure,
license text and LFS content, adapts under the staging root and records actual
source, transfer, cache-reuse and adapted bytes in the result file. Textured real
packages need Pillow: use an environment with the simulation/USD extras or
`uv run --with pillow --no-sync` for this evidence. Do not add Pillow to
`uv.lock`; committed scene previews hash `uv.lock`. The GPU-free worker test uses
a texture-free fixture and needs neither.

### Acquisition commands and browser journey (SF2)

The installed `lw` and `lw mcp` expose the same HTTP records. Start an owned
gateway first (the fixture below serves the real service with a deterministic
runner, no network or worker subprocess):

```powershell
lw acquisition start simready/box_cardboard_a01@524747d9b5e1f21b5b18b56041788c01d745f1a1 --wait --json
lw acquisition ls --status succeeded --json
lw acquisition show acq-<32 hex> --json
lw acquisition cancel acq-<32 hex> --json
lw acquisition retry acq-<32 hex> --json
```

MCP tool names are `acquisition_start`, `acquisition_list`, `acquisition_show`,
`acquisition_cancel` and `acquisition_retry`; they return the same HTTP
documents. `--wait` polls the record for at most 120 seconds. The GPU-free CLI
test starts `tests/fixtures/acquisition_gateway.py`, which stages the committed
`tests/fixtures/acquisition/package`, and compares HTTP, installed `lw` and
official MCP stdio for start, list, show, result, cancel, retry and the
unknown/ineligible/budget errors:

```powershell
uv run --no-sync python -m pytest tests/cli/test_acquisition_commands.py -q
```

The browser journey owns the same fixture gateway and a Vite server, watches
real byte/file progress on an online candidate, cancels, retries, opens the
exact local version link and checks the records section at 400 px:

```powershell
pnpm --filter @spatial-sync/web exec playwright test acquisition.spec.ts
```

Both journeys start from catalog candidate IDs only, never request remote
previews and keep their roots under `.scratch/`.

## Ownership and cleanup

Use isolated ports, processes, worktrees, build directories and evidence.
Foundation integration reserves API8009/web5189. Operations integration reserves
API8010/web5190 while the older user session remains active and protected.
Developer ports8011/8012/8013
and web5191 are separate. The project site uses 5192 for `pnpm site:dev` and
5193 for its build preview. It has its own lockfile outside the workspace:
`pnpm site:install`, then `pnpm site:build`, which the Site workflow checks
outside the GPU-free gate. Local evidence lives in `.scratch/lab-foundation`;
prior process evidence is retained in Git history.

Close owned sessions, stop owned processes and verify listeners and Runner
children are gone. Preserve unrelated services. A retained preview needs an
explicit owner and address. Passing local checks does not claim remote CI.
