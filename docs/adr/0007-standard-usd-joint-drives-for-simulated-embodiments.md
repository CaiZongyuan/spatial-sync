---
status: accepted
date: 2026-11-12
---

# Use standard USD joint drives for simulated embodiments

For the planned embodied training pipeline, simulated mobile and articulated `Embodiment`s (custom-authored or imported, starting with Microduck) will be driven through the platform's standard `UsdPhysics` joint position and velocity target channels (`joint_target_q` and `joint_target_qd` committed once per control tick), rather than importing hardware-specific servo circuit or backlash plugins. Learned `joint_position_offset` `Policy` outputs write target angles to `joint_target_q` while leaving `joint_target_qd` at zero unless an outer `Device Program` commands damping or stop velocities.

We rejected importing hardware-specific servo plugins (such as upstream Microduck's `bam` library) and unattributed external weights in pure simulation because they inject per-substep motor-voltage and series-backlash state into the solver loop—violating the platform's device-agnostic physics port boundary—carry incompatible interpreter constraints, and lack platform `Job` and `Task` provenance. Standard `UsdPhysics` drives keep every new `Embodiment`'s training environment and `NewtonSimulation` consistent in pure simulation while deferring hardware-specific actuator identification to a separate `real` `Embodiment`.

This records an accepted design; the generalized offset action space and reference `Embodiment` are not yet implemented. The complete scope and verification targets are in [Lab Embodied Training](../spec/LAB_EMBODIED_TRAINING_SPEC_V1.md).
