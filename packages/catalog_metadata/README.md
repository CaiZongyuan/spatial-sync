# Local catalog metadata

This package owns Source Collections, pinned HF metadata, organization selections,
manual corrections and immutable Catalog Releases. It imports no Lab World
library, simulation, Blender or device code. The existing API host owns
authorization and its maintenance queue; a worker uses HTTP to submit results.

`MetadataStore(root)` uses `metadata.sqlite3`, content-addressed raw metadata and
a separate image cache. `transaction()` is a trusted host-service write seam for
the maintenance queue; it is never a worker API. Public reads use deferred SQLite
snapshots so a slow publisher or index build does not block the current release.

The input flow is `HFHubReader.enumerate(profile, revision)` followed by
`stage_inventory(collection_id, revision, entries, complete=..., counts=...)`.
HF Dataset identities are unique across registered collections. SimReady reads
package directories/manifests and root object thumbnails; Scenario reads every
metadata shard and keeps example and output-file counts separate; ChemLab
reconciles the release union with the instrument JSON and utensil/furniture
indexes. The latter's publisher facts and original extensions remain attributed
to their evidence. A missing field stays unknown. These facts grant no local
license, adaptation, device or physics admission.

SimReady emits a typed `inventory` progress event as soon as its complete light
identity manifest is known, before per-package extraction, and typed `entry`
events after each extraction. Each also carries cumulative `usage`; ordinary
request progress remains a flat usage document. The host can persist and publish
the complete identity view independently, checkpoint extracted fields, and keep
that view available when a later budget or source error interrupts deep work.

`pending(inventory_id, limit=24)` and `inventory_entries` expose bounded tasks or
the internal complete inventory. `submit_organization` accepts descriptive
fields only, binding the input digest, organization selection revision, manual
correction revision and caller-supplied recipe/skill/vocabulary versions. Supply
`submission_key` for crash-safe retries: the same payload returns its original
response, and a different payload conflicts. `organization_history` retains all
results. `select_organization(..., version=0)` retracts the selection; selecting an
older result increments the selection revision. Manual `correct`/`retract` is an
independent layer and survives future organization. `fieldProvenance` separates
publisher/extracted values, submitted organization and manual choices.

`publish(inventory_ids, release_preparer=...)` retains other selected collections,
rejects incomplete enumeration, builds a deterministic lexical index, and binds
all input versions, composed fields, correction selections and vocabulary/index
digests. The host callback receives the final release document and entries to
prepare its actual search indexes before activation. The callback also receives
`vocabulary=` with the candidate's pinned vocabulary, including during restore
while ordinary readers still see the old database. Failure leaves the old
release active. `release`, `entries` and `entry` retain historical releases and
exact source references; no local Asset, Scene or Recording is changed.

`export`/`restore` move metadata, retained selections and bounded cached evidence.
Restore verifies input/release/index identities, counts and references, writes
blobs before activation, and supports the same `release_preparer` callback.
It replaces only this package's metadata tables, preserving host queue tables
and all local Assets. A failed restore keeps the prior release active.

Reader defaults are 10,000 requests, 256 MiB body transfer, 2 MiB per ordinary
document/image, 32 MiB per Parquet table, 20-second requests, three attempts,
30 minutes and 2 GiB of cached images. External background image-body prefetch
defaults to zero. Demand previews need an explicit `previewBytes` allowance.
Images can be evicted without deleting raw metadata, corrections or releases.
Explicit image bodies and compressed Parquet preview columns count toward actual
`previewBytes`; `embeddedPreviewDecodedBytes` reports decoded image sizes
separately. Bundled table previews use the bounded table/network budgets and do
not enable separate image requests. Cached reuse reports zero new transfer.
Model usage remains unknown when no runtime receipt supplies it.

Scenario Parquet decoding requires `pyarrow` in the explicitly configured worker
environment. No decoder is installed or downloaded during maintenance. An absent
decoder reports `table_decoder_unconfigured`. For an owned manual environment:

```powershell
uv run --no-project --python 3.13 --with pyarrow --with httpx python <owned-worker-entrypoint>
```

The host-only `LW_CATALOG_HF_ENDPOINT` can select a trusted HTTPS endpoint or an
explicit loopback HTTP fixture for subprocess verification. Profiles cannot
choose arbitrary URL proxies. Primary model formats are rejected by the reader.
The deterministic JSON table fixture tests the Scenario row contract without an
optional decoder in ordinary CI; real Parquet decoding and coverage have separate
versioned evidence. All default profiles are disabled until explicitly enabled.
