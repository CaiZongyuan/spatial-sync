---
status: accepted
date: 2026-11-12
---

# Add MuJoCo Warp (`mjlab` + `rsl_rl`) as a compute-node training backend

For the planned embodied training pipeline, compute nodes will support a lightweight Linux-native MuJoCo Warp (`mjlab` + `rsl_rl` PPO) training backend alongside `isaaclab`, behind the same `Job`, heartbeat, TensorBoard, checkpoint and ONNX export contracts. Platform `Evaluation` continues to run in `NewtonSimulation` under fixed comparable conditions over CPU `onnxruntime`.

This partially amends [ADR-0001](0001-usd-canonical-scene-format.md): USD (`UsdPhysics`) remains the sole authored canonical format for `Asset`s and `Scene Version`s—never maintaining a second hand-authored robot or scene definition—while the MuJoCo Warp trainer deterministically derives its solver specification from the pinned USD `Asset` and `Scene Version` at build or launch time, analogous to how browser `glTF` visuals derive from USD.

We chose this backend as a general training path for custom-authored and imported articulated `Embodiment`s (starting with bipedal Microduck) because `NewtonSimulation` runs `SolverMuJoCo` (MuJoCo Warp) as its contact and articulation solver, so `Policy` packages trained in MuJoCo Warp transfer to platform `Evaluation` and interactive `NewtonSimulation` with minimal solver-to-solver gap while running natively on a single workstation GPU without Omniverse/Kit runtime overhead or cross-OS process bridges.

## Considered Options

- **Isaac Lab (`isaaclab`) only**: preserves a single trainer backend, but couples lightweight embodiments to Omniverse/PhysX startup overhead and introduces a PhysX-to-MuJoCo contact gap against `NewtonSimulation(solver="mujoco")`.
- **Standalone external training scripts**: faster to run once, but bypasses platform `Job` tracking, `Training Plan` budgets, checkpoint provenance and reproducible `Policy` qualification.

This records an accepted design; the `mjlab` compute-node backend is not yet implemented. The complete scope and verification targets are in [Lab Embodied Training](../spec/LAB_EMBODIED_TRAINING_SPEC_V1.md).
