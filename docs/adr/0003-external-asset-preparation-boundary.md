---
status: accepted
date: 2026-10-23
---

# External material is prepared into local Asset versions

For the planned external acquisition feature, the platform retrieves selected Source Packages and performs supported, deterministic preparation and validation before importing local Asset versions. Preparation may arrange dependencies, normalize supported USD structures and derive browser visuals; model repairs, missing collision authoring and device behavior remain work for an Asset Factory. Unsupported preparation must expose its gaps. This lets people and agents acquire supported items through one operation without making the platform a general modeling tool.

Imported Assets remain self-contained and immutable. Acquisition pins an upstream revision and preserves provenance and applicable license notices. Upstream updates are acquired deliberately; unchanged prepared content reuses its local version, and changed content creates a new one. Existing scene references keep their selected versions. Download caches can be managed separately without breaking imported Assets.

We considered leaving all preparation to an external Asset Factory, which preserves a smaller platform but requires manual steps for every acquisition, and retaining live remote references, which reduces local storage but makes existing scenes depend on upstream availability and changes. We chose bounded preparation and local versions for repeatable, offline use. This is an accepted design boundary; the acquisition feature is not yet implemented.

The complete accepted scope and delivery criteria are in [Lab Scene Factory](../spec/LAB_SCENE_FACTORY_SPEC_V1.md).
