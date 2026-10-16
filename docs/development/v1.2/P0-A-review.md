# P0-A integration review

Reviewer: PM (`/root`), non-author, both Standards and Spec; shared coordination context. Baseline: `633735534aa05376138904582ebb7712a0ea19c9`, shared protocol `80b5b1d`; initial author candidate `c8ed65e`, repaired candidate `cdbd039dcf046697abce19b2bb06af3d142270cc`. Local WIP integration: `78f07c6e709c5a294cb431be4ffe2c60594cea48`.

Standards: no blocking finding after reading simulation, scenes, loaders, labels, Site generation, Manifest exporter, reference/smoke tools, tests, dependency configuration and evidence. Newton imports stay out of the protocol; pinned-engine behavior is isolated in the runtime. The exporter preserves world-body versus local-shape transforms and identifies global static shapes separately. Reference-tool access to a pinned private Viser hook is explicitly documented, local-only and tested; it remains a version-upgrade maintenance concern.

Spec: two initial findings repaired by the original author before integration. Missing-label warnings now use `unlabeled_entity`. Anchor generation reuses an existing same-name child-side Newton Site, rejects conflicting non-Site/ambiguous/wrong-body entries, and exports its joint-anchor role; a real-engine regression test independently composes its world pose. PM read the repaired delta and the new `test_sites.py` coverage.

Reused evidence: 66 baseline Python tests, scoped Ruff, lock and whitespace checks; actual CPU/CUDA step/reset receipts and bounded ViewerViser runs. Nine affected tests plus scoped checks passed after the warning/Site repair. These are author execution receipts, not duplicated independent executions. PM independently installed the integrated default environment with `uv sync --frozen` and verified Pydantic 2.12.4 is present and Newton is absent.

No outstanding P0-A blocker. Async extraction, IPC, realtime pacing, source lifecycle and browser parity remain P0-E acceptance. Imported missing-label provenance beyond the supported construction adapters is an explicit compatibility limitation, not a spelling heuristic. Final milestone reviews must cover the integrated runtime/gateway/browser consumer path.
