---
status: accepted
date: 2026-11-12
---

# Add MuJoCo Warp (`mjlab` + `rsl_rl`) as a compute-node training backend

Compute nodes support a lightweight Linux-native MuJoCo Warp (`mjlab` + `rsl_rl` PPO) training backend alongside `isaaclab`, behind the same Job, heartbeat, TensorBoard, checkpoint and ONNX export contracts. USD remains the canonical format for Assets and Scene Versions ([ADR-0001](0001-usd-canonical-scene-format.md)); solver-specific training representations are derived from or verified against the pinned USD Asset, and platform Evaluation continues to run exclusively in `NewtonSimulation` over CPU `onnxruntime`.

We chose this backend as a general training path for custom-authored and imported articulated Embodiments (starting with bipedal Microduck) because `NewtonSimulation` runs `SolverMuJoCo` (MuJoCo Warp) as its contact and articulation solver, so policies trained in MuJoCo Warp transfer to platform Evaluation and interactive `NewtonSimulation` (`cpu` or `cuda`) with minimal solver-to-solver gap while running natively on a single workstation GPU without Omniverse/Kit runtime overhead or cross-OS process bridges.

## Considered Options

- **Isaac Lab (`isaaclab`) only**: preserves a single trainer runner, but couples lightweight embodiments to Omniverse/PhysX startup overhead and introduces a PhysX-to-MuJoCo contact gap against `NewtonSimulation(solver="mujoco")`.
- **Standalone upstream training scripts outside `apps/node`**: faster to run once, but bypasses platform Job tracking, Training Plan budgets, checkpoint provenance and reproducible Policy qualification.
