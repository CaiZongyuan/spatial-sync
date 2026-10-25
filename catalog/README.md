# SimReady catalog

`catalog/simready.json` is the curated, committed subset of the five SF0
`nvidia/simready-assets` packages. It is the offline-readable source for
catalog search, entry describe and read-only acquisition estimates until a full
sync is published. Search never contacts Hugging Face; an explicitly triggered
catalog update reads only its lightweight directory. Previews load when visible,
and source detail documents load when an entry or estimate is opened. The
gateway never imports `factory/`.

The snapshot identity is `sha256:` plus the SHA-256 of the exact bytes of this
file. `indexedAt` is a fixed curation timestamp, not the file modification time.

## Full directory sync

In the Web asset library select **在线** or **全部**, then **更新目录**.
The same operations are available through HTTP, installed CLI and MCP:

```powershell
lw catalog sync --json
lw catalog sync --provider simready --wait --json
lw catalog sync-status --json
lw catalog sync-status --id <sync-id> --json
lw catalog sync-cancel <sync-id> --json
lw catalog sync-retry <sync-id> --json
```

`sync --wait` polls for at most 120 seconds; a larger sync continues in the
gateway and can be observed with `sync-status`. The HTTP base is
`/api/v1/library/catalog/sync`: GET reads current progress/coverage, POST starts
a sync, `/{id}` reads history, and POST `/{id}/cancel` or `/{id}/retry` controls
an attempt. Mutations require operator access. MCP tools use the names
`catalog_sync`, `catalog_sync_status`, `catalog_sync_cancel`, `catalog_sync_retry`.

One gateway-owned attempt runs at a time. It pins a source commit, follows every
directory page and indexes every package identity, name and tree hash. It never
reads per-package inventories, detail documents or images. Search and pagination
use the published index without source requests. The browser attaches a preview
URL only when a card intersects the viewport, and an explicit click loads the
next page. A visible preview reads only that package's inventory and smallest
image; the packaging declaration is read when details/estimate are opened.
Source previews are served through the registered adapter at
`/api/v1/library/catalog/entries/{provider}/{package}/{revision}/preview`.
The old content-addressed preview route remains readable. A preview does not
establish admission, physics or task capability. Main USDs and textures remain
part of explicit acquisition.

State lives under `<SPATIAL_STATE_ROOT>/catalog`: content-addressed preview and
metadata caches, immutable directory snapshots, persistent sync receipts, and
an atomic current publication pointer. Package tree hashes reuse unchanged
metadata/images across runs. Only published snapshots are used for historical
exact-version lookup. Cancel, failure and restart retain the prior published
index; an unfinished attempt becomes interrupted after restart. A failed or
absent sync leaves the five-entry seed usable offline.

The collection format is `lw.asset-catalog/3`, containing independent source
snapshots. Versions 1 and 2 remain readable. Discovery-only entries may have
unknown source sizes, kind, dimensions,
license evidence and usage. Unknowns never satisfy hard filters. A source license
declaration is displayed separately from admission, and unreviewed entries
cannot acquire. The trusted seed overlay applies only at its reviewed source
commit; a new commit never inherits its qualification. Old exact-version
descriptions and already imported Assets remain readable.

Defaults: eight workers, 20-second request timeout, three attempts, 2 MiB per
metadata document/image and 2 GiB each for transfer and the independent discovery
cache, which evicts demand-loaded content by last use. Re-requesting evicted
content loads it again; imported Assets and snapshots are never evicted. Concurrent
reads of the same package collapse. Demand reads use their own eight-worker API
capacity so slow image requests do not occupy the search thread capacity.
HTTP 429 shares backoff across indexing and content reads for that provider.
Transfer counts response-body bytes, including retries. Source-inventory logical
bytes, actual transfer and local cache usage are separate. Historical published
snapshots and installed Assets are outside the discovery cache budget.

## Additional sources

`packages/catalog/sources.py` registers each provider, dataset and adapter factory.
Pass registrations as `create_app(catalog_sources=[CatalogSource(...)])`;
`simready` is the default when this parameter is omitted. Adapters own outbound
URLs and expose fixed revision, lightweight package enumeration, package link,
preview inventory, full metadata and bounded image reads. Registering a source
does not admit its assets for acquisition.

Search identities, resource caches and source filters include provider. Same-name
packages from different sources remain separate. `sync --provider <name>` (HTTP
POST `?provider=<name>`, MCP optional `provider`) updates one source; omit it to
update all registered sources. Failures preserve that source's previous snapshot;
other sources may still publish. Source results and failures are visible in status.
The actual shipping integration remains SimReady; tests exercise a second adapter
registration, name collisions, caching and isolated failure.

Search also builds an offline semantic index over bilingual names, aliases,
descriptions, tags and usage facts. The default `lw-hash-concept-standin` is a
deterministic CI stand-in, labelled `deterministic-stand-in` and `real: false`
in every response; it is not model-quality evidence. A real CPU multilingual
embedder can be supplied through `LW_CATALOG_EMBEDDER` for separate evidence
runs: `stand-in` (default), `fastembed:<model>` or
`sentence-transformers:<model>`, with optional `LW_CATALOG_EMBEDDER_MODEL`,
`LW_CATALOG_EMBEDDER_CACHE` and `LW_CATALOG_EMBEDDER_THREADS`. A model that
cannot load falls back to the labelled stand-in instead of failing search.
Semantic recall only fills a gap after keyword matching misses; hard filters,
exact IDs, cursors, snapshots and response budgets are unchanged.

The SF1b evaluation corpus lives in `tests/fixtures/catalog/semantic/`: five
real SimReady records copied unchanged plus 100 synthetic bilingual fixture
records, 40 queries with a frozen retained split, snapshot ID and user-confirmed
relevance labels. Synthetic source, license, size, adaptation and usage fields
are fixtures, not source facts. Use `tools/eval_catalog_semantic.py` for the
per-language metrics; the deterministic stand-in is not a real-model result.

Every fact about a package (source file/byte counts, BOM membership, license
evidence, adaptation result, upstream declarations, usage and eligibility) must
agree with `factory/support.json`. After changing either file, re-verify the two
files together and run the catalog tests:

```sh
uv run --no-sync python -m pytest tests/unit/test_catalog.py -q
```
