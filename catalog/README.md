# SimReady catalog

`catalog/simready.json` is the curated, committed subset of the five SF0
`nvidia/simready-assets` packages. It is the offline-readable source for
catalog search, entry describe and read-only acquisition estimates; the gateway
never contacts Hugging Face and never imports `factory/`.

The snapshot identity is `sha256:` plus the SHA-256 of the exact bytes of this
file. `indexedAt` is a fixed curation timestamp, not the file modification time.

Every fact about a package (source file/byte counts, BOM membership, license
evidence, adaptation result, upstream declarations, usage and eligibility) must
agree with `factory/support.json`. After changing either file, re-verify the two
files together and run the catalog tests:

```sh
uv run --no-sync python -m pytest tests/unit/test_catalog.py -q
```
