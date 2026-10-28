---
status: accepted
date: 2026-10-28
---

# Store the local asset catalog in the repository

The default workbench keeps the three HF source catalogs as versioned JSONL and
deduplicated WebP in the repository. A generated, ignored SQLite cache serves the
existing Web/CLI/MCP query paths. This follows the user's 2026-10-28 scope revision
after the earlier supervisor-based design accumulated history and duplicated
evidence far beyond the small discovery dataset.

Source revisions identify upstream content; source declarations and Agent
descriptions retain their meaning and do not grant acquisition or physical-use
admission. Imported Assets, Scenes and recordings keep their own precise versions.

An explicit source update reads a pinned revision, preserves applicable organization
and rejects extraction failures before replacing the selected source. Candidates
are prepared before activation; the committed SQLite replacement is the runtime
visibility point. A local file lock coordinates updates and cold cache verification,
while active readers keep using the old complete database until commit. Unified
catalog reads refresh to the committed version and remain fixed for one request.

Legacy MetadataStore deployments and the standalone host retain their existing
interfaces when explicitly configured. Their worker, evidence and historical-release
model is separate from the default lightweight workflow. Current behavior and
commands are documented in [the maintenance guide](../CATALOG_MAINTENANCE.md) and
[the current specification](../spec/LAB_ASSET_METADATA_PLATFORM_SPEC_V1.md).
