# Local asset catalog

The default Lab host reads the three source JSONL files and WebP thumbnails
tracked under `catalog/data/` and `catalog/thumbnails/`. Its generated SQLite
cache and short sync status live in the ignored `catalog/local/` directory.
Web, CLI and MCP share this catalog without a separate maintenance service.

```powershell
uv run --no-sync python tools/sync_catalog.py --rebuild --verify --check-thumbnails
uv run --no-sync python tools/sync_catalog.py --sync-source simready --revision <40-hex>
```

Source sync reads the selected pinned revision through the existing recipe.
Any entry extraction failure retains the previous source and records the failed
identities in `catalog/local/sync_status.json`. An unchanged entry reuses its
organized description and local thumbnail. Candidate files and SQLite are
prepared together; runtime readers switch only when the verified SQLite file
is committed. New processes serialize initial cache verification with updates.
The running unified search and native metadata reads both follow the committed
version; old page references explicitly expire after an update.

After editing JSONL or pulling newer tracked data, run `--rebuild`, or restart
the host to verify and rebuild the cache. An ordinary running query does not
publish partially edited source files. Default Web pages hide legacy sync and
maintenance controls; their write endpoints return `409 not_supported`.
Agent-authored names, summaries and tags belong in the JSONL; the Agent performs
the organization in its own session and preserves unknown technical fields.

See [catalog/README.md](../catalog/README.md) for the layout and
[the current specification](spec/LAB_ASSET_METADATA_PLATFORM_SPEC_V1.md) for scope.

## Explicit legacy metadata hosts

The remaining sections describe a deployment explicitly using `MetadataStore`
or the standalone legacy host. Their runtime, history and evidence requirements
do not apply to the default repository catalog.

The existing `catalog sync` remains the lightweight directory update. Metadata maintenance is a separate, opt-in workflow over registered Hugging Face Source Collections. It uses the same gateway, a local SQLite database and an owned worker subprocess; it needs no Newton, Blender, GPU or external database.

The [maintenance architecture](design/CATALOG_DATA_MAINTENANCE.md) defines per-entry organization, progressive reading, image derivatives and quality checks. The self-contained [maintenance skill](../skills/lab-catalog-maintain/SKILL.md) is the executable workflow for another Agent. Whole-source collection and real organization quality remain a separately selected data task.

Start the gateway using the README command and enable only the selected collection. `lw catalog maintain simready --wait --json` retains configured-Runtime behavior; no configured Runtime leaves organization pending. Use `--organizer external` to accept batches from another Agent, or `--organizer none` for source/image maintenance alone. Search reads an immutable release during all maintenance; inventory, extraction, organization and image coverage are separate.

## External Agent workflow

Select the collection/revision and caller-owned model allowance first. Write `enabled.yaml` with `enabled: true`; a budget file may bound requests, transfer, metadata/table/image bytes, retained thumbnail increments and wall time. In external mode omit `modelTokens` or set it to 0: the service dispatches no model and the caller owns model spending. Runtime mode retains the service's token budget and actual execution receipts.

```powershell
lw catalog collection-set simready enabled.yaml --json
lw catalog maintain simready --organizer external --previews --revision <commit> --budgets budgets.yaml --wait --json
lw catalog maintenance-status --id <run-id> --json
lw catalog batch-claim <run-id> --limit 8 --json
lw catalog batch-submit <run-id> <batch-id> receipt.json --json
lw catalog maintenance-finish <run-id> --json
```

Wait for `phase: awaiting_agent` before claiming. The source worker has exited by then; it does not compete with the external Agent or invoke an available Runtime. Preserve each claim's `inputKey`, exact entry identities/digests and version bindings. The claim supplies current category terms and bounded small evidence texts alongside extracted source fields. Repeat claim/submit until empty, then finish to publish. The skill documents descriptive fields, honest unknowns, item errors, native usage, correction conflicts and recovery.

External receipts retain unknown usage, known external batch counts and optional caller-reported tokens; submission is not counted as service-owned model execution. It therefore does not require a provider SDK or Runtime integration. In Runtime mode unknown execution usage still stops further calls. Source/network/image/storage and original wall-clock limits apply to both paths; a resume cannot extend the original deadline. A new bounded retry reuses valid retained work.

## Progressive native reads

These commands operate on published source metadata in either host:

```powershell
lw catalog metadata-search --collection simready --q <query> --json
lw catalog metadata-show <entry-id> --release <release> --json
lw catalog metadata-show <entry-id> --release <release> --section facts --document-digest <digest> --json
lw catalog metadata-show <entry-id> --release <release> --section sourceMetadata --offset 8192 --document-digest <digest> --json
lw catalog thumbnail <entry-id> --release <release> --output .scratch/preview.webp --json
```

Search uses literal terms, exact identity, collection/category/tag filters and a cursor bound to the immutable release. Names, aliases, summaries, tags, domains and uses participate. Summaries are bounded to 8 KiB; selected sections carry byte continuations and encoding, with responses bounded to 32 KiB. Follow the returned `nextOffset`, not a guessed character index. Source facts retain their provenance. Lab's existing unified `catalog search/show/estimate` adds installed versions, acquisition policy and physical-use filters outside this module.

The Web detail initially reads a summary and requests full evidence metadata only when expanded; individual source evidence remains an explicit read. Search and metadata reads do not trigger upstream acquisition.

## Local WebP

`--previews` performs a resumable image phase after source extraction. Without an explicit `previewBytes` it allows 16 MiB of external image bodies for that run. Limits can rise explicitly to 512 MiB of image transfer within the 1 GiB maximum total transfer. The default remains no external image prefetch. Demand preview reads have their own per-read bound and do not count as that worker run's transfers.

Both catalog paths use one deterministic transform: maximum edge 384 px, no upscaling, WebP quality 78/method 4, preserved alpha and EXIF orientation. Inputs are PNG/JPEG/WebP, at most 2 MiB and 16 Mi pixels; outputs are at most 512 KiB. Object, material and normal roles remain distinct. Missing/failed/suspect states remain visible.

Source image SHA, dimensions and bytes are separate from the output SHA and transform profile. Embedded Scenario previews name their table/row/field and retain the separate original-preview reference. Repository-object identity or a revision/path fallback prevents unproven reuse across source revisions.

Retained derivatives live outside disposable source-image cache. Publications pin their exact digest; old source/cache references remain readable. The state root has a 512 MiB retained-derivative limit; `thumbnailBytes` can further lower newly retained bytes for one run. Registration, queue settlement and quota updates share one writer transaction. An explicit repair can reproduce a missing/corrupt derivative from its source without changing a publication's digest. Image reads use separate capacity so waiting previews cannot consume native-query threads.

Backup export emits `lw.catalog-metadata/2`, including retained image bytes and bindings. Restore also reads legacy v1, validates images and references before activation and preserves the existing release on failure. JSON export retains the existing 256 MiB evidence bound and reports exhaustion explicitly. Restore does not change imported Assets, scenes, recordings or host credentials.

## Lab host library location

The embedded Lab host serves the lightweight repository catalog from `<REPO_ROOT>/catalog`
by default, backed by versioned `catalog/data/*.jsonl` files and `catalog/thumbnails/*.webp`
images, with a local rebuildable SQLite cache in `catalog/local/catalog.sqlite3`.

A deployment that maintains an external or custom metadata library elsewhere can override
this location with:

```powershell
$env:SPATIAL_CATALOG_METADATA_ROOT = 'C:/Users/<name>/.spatial-sync/state/catalog-metadata'
```

Or point to an alternative lightweight catalog directory with:

```powershell
$env:SPATIAL_CATALOG_ROOT = 'D:/path/to/catalog'
```

Resolution order is an explicitly injected store (tests and fixtures), then
`SPATIAL_CATALOG_METADATA_ROOT` (if set to an existing directory), then
`SPATIAL_CATALOG_ROOT`, and finally the repository default (`<REPO_ROOT>/catalog`).
An explicit `SPATIAL_CATALOG_METADATA_ROOT` must be an absolute path naming an existing
directory. Leaving it unset keeps test fixtures isolated: `tests/conftest.py` clears
both variables so tests never inherit a developer's real library.

Startup logs the effective directory, its resolution source and the release being served.
The sync and rebuild utility is `tools/sync_catalog.py`: `--sync-source <id>
[--revision <sha>]` performs a real fixed-revision read through the source
recipes and activates verified candidates; `--rebuild` only regenerates the local
SQLite cache from `catalog/data/`.

This lightweight host has no maintenance runs or collection switches. Its status
reports `capabilities.maintenance=false`, the Web asset page omits those controls,
and run, collection and organization mutations answer `409 not_supported` instead
of failing inside the router. Use the sync utility for source updates and keep the
full maintenance platform for explicitly configured `MetadataStore` deployments.

## Independent host

The HTTP implementation lives in `packages/catalog_metadata/http.py`. Both the Lab host and standalone `service.create_app` mount it; Lab auth and index preparation are injected. The core imports no Lab application, device, acquisition or simulation code. `requirements.txt` beside the module pins the minimal HTTP/image dependencies; optional Scenario decoding needs a prepared worker interpreter with `pyarrow`.

From this checkout:

```powershell
uv run --no-sync python -m packages.catalog_metadata --port 8011
$env:LW_URL = 'http://127.0.0.1:8011'
lw catalog collections --json
```

`LW_CATALOG_STATE_ROOT` selects standalone state, defaulting to `~/.lw-catalog/state`; it does not automatically attach the Lab library. `LW_CATALOG_OPERATOR_TOKEN` protects remote access; CLI uses `LW_TOKEN`. Non-loopback serving requires a credential. `LW_CATALOG_SKILL` can mount an explicit versioned instruction file; installed wheels otherwise use their bundled skill. One host leases each state root, preventing another supervisor from interrupting its active runs. Consumers use HTTP rather than sharing that SQLite directory.

Independent deployment needs this module, its minimal requirements and the versioned skill resource; no Lab service or model SDK is required. Lab continues to use the same module embedded by default. A later remote Lab adapter can consume the native immutable release interface while retaining Lab-specific admission and local Asset ownership.

The HTTP entrypoint is `/api/v1/library/catalog/maintenance`. It exposes collections, candidate discovery, runs and checkpoints, bounded claims/submissions, source evidence, corrections, organization selection, publication and backup. Web, CLI and MCP use the same routes. Maintenance writes require the existing operator role; a worker's scoped token can mutate only its active run. Tokens and runtime command arguments are never returned in status documents.

Configure an actual runtime through the trusted server environment `LW_CATALOG_AGENT_PROFILE`, a JSON object with `name`, `model` and `command` (an argv array), plus optional `timeoutSeconds` (default 120) and `maxOutputBytes` (default 1048576). The command receives the versioned maintenance skill and one bounded JSON batch on stdin. It emits the structured receipt described in the shipped `lab-catalog-maintain` skill on stdout. The runtime owns its credentials, model/tool context and native session. No runtime is chosen or paid call made when this setting is absent.

For a deterministic local integration fixture, the profile command can use the current Python interpreter and `tests/fixtures/catalog_metadata_agent.py`; the fixture labels its model as a deterministic substitute. This proves process/HTTP behavior and does not establish real model organization quality.

For sources with a lightweight complete manifest, the worker publishes that inventory before reading deep per-item metadata. Extraction updates mutable checkpoints in batches of at most 24 entries; a final or budgeted partial phase publishes one consistent replacement. Coverage distinguishes identities, successful extraction, extraction failures, remaining metadata and organization. Exhausting the deep-read budget retains the complete searchable listing and its explicit gaps. Existing pagination keeps its original release.

Default Runtime caps remain 10,000 source requests, 256 MiB source transfer, 2 MiB per metadata document, 32 MiB per table, zero external image prefetch, 100,000 reported model tokens and 30 minutes. Request timeout is 20 seconds with at most three attempts. Explicit budgets can raise total transfer to 1 GiB, images to 512 MiB and wall time to 24 hours, or lower limits. Embedded table preview transfer is counted separately. Runtime commands honor remaining tokens; the supervisor bounds process time/output and checks actual receipt usage before continuing. External mode has no service-owned model allowance.

`LW_CATALOG_WORKER_URL` can specify the owned loopback HTTP address for scheduled starts; manual starts derive the callback from the actual server socket. `LW_CATALOG_WORKER_PYTHON` can select an explicitly prepared Python interpreter for optional table decoding, with the same gateway dependencies and `pyarrow` installed. The default interpreter does not download missing decoders. Missing table support reports configuration needed rather than claiming complete Scenario coverage.

Workers write only bounded raw source/cache files and use scoped HTTP for catalog mutations. Durable batch receipts precede organization commits, allowing a gateway restart to replay the same input/submission keys without another model call. Cancel and shutdown signal the owned worker through its control file so it can close its runtime child; the supervisor terminates only its own handle if cooperative shutdown fails. Interrupted runs require explicit resume. Collection schedules (`scheduleSeconds`, 0 disabled, otherwise 60 seconds to seven days) run only while the gateway is alive and the collection is enabled.

An interrupted Runtime execution without a final receipt keeps its usage unknown. Start a bounded retry instead of continuing that unknown service-owned allowance. External mode records caller-owned usage without inventing service model calls. Source requests/bytes and Runtime allowance accumulate across resume; completed cache and organization inputs remain reusable on retry.

The supervisor admits at most two worker trees and one claimed or executing batch per run. It derives model totals from durable batch receipts, and resumes revoke earlier unsubmitted claims. Windows uses a kill-on-close Job attached through the creating subprocess handle; POSIX uses an owned process group. A readiness handshake precedes source/model work, and worker failure closes remaining runtime descendants. Repeating the same source revision retains its published rich fields while the next bounded extraction runs.

The metadata backup contains metadata, historical releases, corrections and cached evidence. It does not copy imported Assets, scenes, recordings or runtime credentials. Restoring metadata cannot change existing physical versions. Source revision, organization revision, correction selection and local Asset revision remain independent.
