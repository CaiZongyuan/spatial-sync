---
status: accepted
date: 2026-10-23
updated: 2026-11-19
---

# External material is prepared into local Asset versions

For the planned external acquisition feature, the platform retrieves selected Source Packages and performs supported, deterministic preparation and validation before importing local Asset versions. Preparation may arrange dependencies, normalize supported USD structures and derive browser visuals; model repairs, missing collision authoring and device behavior remain work for an Asset Factory. Unsupported preparation must expose its gaps. This lets people and agents acquire supported items through one operation without making the platform a general modeling tool.

Imported Assets remain self-contained and immutable. Acquisition pins an upstream revision and preserves provenance and applicable license notices. Upstream updates are acquired deliberately; unchanged prepared content reuses its local version, and changed content creates a new one. Existing scene references keep their selected versions. Download caches can be managed separately without breaking imported Assets.

We considered leaving all preparation to an external Asset Factory, which preserves a smaller platform but requires manual steps for every acquisition, and retaining live remote references, which reduces local storage but makes existing scenes depend on upstream availability and changes. We chose bounded preparation and local versions for repeatable, offline use.

The complete accepted scope and delivery criteria are in [Lab Scene Factory](../spec/LAB_SCENE_FACTORY_SPEC_V1.md).

## Amendment (2026-11-19) — External robot import and baked calibration

To let users and Agents onboard external robots without repository packaging scripts, the platform extends bounded preparation to uploaded `URDF`, `MJCF` and `USD` robot archives with rigid-body meshes. It bakes non-geometric calibration into a new immutable `Z-up` OpenUSD `Asset` version and preserves the source-to-canonical joint mapping so simulation and training derive their models from the same calibrated source.

### Consequences

- Validation compares the Newton model with the derived `mjlab` solver model and explicitly rejects unsupported structures in the `Validation Report`. [ADR-0006](0006-mujoco-warp-training-backend.md) · [ADR-0007](0007-standard-usd-joint-drives-for-simulated-embodiments.md) · [Import and calibration contract](../spec/LAB_SELF_SERVICE_EMBODIED_SPEC_V1.md#双侧物理模型一致性与-goal-冻结点)
- The first approved `Training Plan` freezes the calibrated `Asset` version for its `Goal`. A calibration change requires a new `Goal`.
