# SimReady catalog

`catalog/simready.json` is the curated, committed subset of the five SF0
`nvidia/simready-assets` packages. It is the offline-readable source for
catalog search, entry describe and read-only acquisition estimates; the gateway
never contacts Hugging Face and never imports `factory/`.

The snapshot identity is `sha256:` plus the SHA-256 of the exact bytes of this
file. `indexedAt` is a fixed curation timestamp, not the file modification time.

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
