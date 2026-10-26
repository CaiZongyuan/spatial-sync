---
status: accepted
date: 2026-10-26
---

# Maintain source metadata independently and publish shared catalog releases

The external catalog maintains source collections and their metadata as a separate domain, stored locally and consumed by people, agents and Lab World. Background agents use versioned maintenance skills and bounded platform tools to build complete inventories, normalize source information and enrich search descriptions; a service owns durable writes, evidence, task progress and publication. Published metadata and indexes share a catalog release, while source revisions, agent annotations and local Asset versions retain separate identities.

Hugging Face Dataset is the primary collection identity. GitHub, project sites and Spaces normally supply associated instructions, catalogs or browsing resources. A shared Hub connector and small dataset-specific parsing recipes handle the first three actual collection structures. The first delivery uses the existing API host, local SQLite and ordinary files, with a separate background worker; the domain boundary does not require separate HTTP deployments, brokers or network databases.

We considered extending the current browse-time cache so each opened detail gradually fills the catalog. That preserves a smaller implementation but leaves unvisited items without useful metadata, makes progress depend on browsing and cannot demonstrate whole-dataset coverage. We also considered letting agents edit search files directly, which is flexible but gives no consistent publication, retry or correction boundary. A maintained catalog with structured submissions supports automatic background work and repeatable retrieval, at the cost of a local task supervisor, a durable metadata store and explicit release handling.

Hosting platforms and collection-specific parsers remain separate. Existing structured metadata is imported deterministically before agent enrichment. Publisher statements, agent descriptions, human corrections and actual validation retain their provenance; discovery never implies that an external item is an installed Asset or approved for acquisition. Lab-specific physics, behavior and immutable imports remain with Lab World.

The user accepted this direction on 2026-10-26 and requested a decoupled, lightweight specification. The implementation uses the existing API host and a separate owned worker. Runtime configuration and the distinction between inventory, extraction and Agent organization coverage are documented in [the maintenance guide](../CATALOG_MAINTENANCE.md). The scope, skills contracts and verification decisions are in [the metadata platform specification](../spec/LAB_ASSET_METADATA_PLATFORM_SPEC_V1.md).
