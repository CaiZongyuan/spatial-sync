---
status: accepted
date: 2026-10-23
updated: 2026-11-19
---

# External material is prepared into local Asset versions

For the planned external acquisition feature, the platform retrieves selected Source Packages and performs supported, deterministic preparation and validation before importing local Asset versions. Preparation may arrange dependencies, normalize supported USD structures and derive browser visuals; model repairs, missing collision authoring and device behavior remain work for an Asset Factory. Unsupported preparation must expose its gaps. This lets people and agents acquire supported items through one operation without making the platform a general modeling tool.

Imported Assets remain self-contained and immutable. Acquisition pins an upstream revision and preserves provenance and applicable license notices. Upstream updates are acquired deliberately; unchanged prepared content reuses its local version, and changed content creates a new one. Existing scene references keep their selected versions. Download caches can be managed separately without breaking imported Assets.

We considered leaving all preparation to an external Asset Factory, which preserves a smaller platform but requires manual steps for every acquisition, and retaining live remote references, which reduces local storage but makes existing scenes depend on upstream availability and changes. We chose bounded preparation and local versions for repeatable, offline use. This is an accepted design boundary; the acquisition feature is not yet implemented.

The complete accepted scope and delivery criteria are in [Lab Scene Factory](../spec/LAB_SCENE_FACTORY_SPEC_V1.md).

## Amendment (2026-11-19): External robot interchange onboarding and baked calibration

To let users and Agents onboard external robots without writing repository packaging scripts while keeping direct 3D mesh and freeform CAD modeling outside the platform, bounded platform preparation is extended to uploaded robot interchange archives (`URDF`, `MJCF` and `USD` with rigid-body meshes).

Non-geometric onboarding calibrations—global up-axis and unit-scale normalization, volume-based mass and inertia estimation when omitted (`massSource: estimated`), link-pair collision exclusions (`FilteredPairsAPI`), contact friction (`MaterialAPI`), canonical joint armature attributes, standard `UsdPhysics` position/velocity drive parameters ([ADR-0007](0007-standard-usd-joint-drives-for-simulated-embodiments.md)), default standing postures, source-joint name/axis-sign/zero-offset mappings, and `Binding Template` drafts—are **baked into a new immutable `Z-up` OpenUSD `Asset` revision** rather than applied as mutable runtime overlays. Validation compares the interactive Newton physics representation and the `mjlab` derived solver representation ([ADR-0006](0006-mujoco-warp-training-backend.md)) on mass, inertia, joint axes/limits, collision exclusions and friction, and records unsupported source constructs (such as closed kinematic loops, spherical/mimic joints or open collision meshes) explicitly in the `Validation Report` instead of silently dropping them. Once a first `Training Plan` is approved for a `Goal`, the robot `Asset` revision is frozen for that `Goal`; subsequent calibration revisions start a new `Goal`.
