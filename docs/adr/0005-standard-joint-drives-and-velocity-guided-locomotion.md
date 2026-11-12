---
status: accepted
date: 2026-11-12
---

# Use standard USD joint drives and hierarchical velocity-guided locomotion for mobile embodiments

Simulated mobile and articulated Embodiments (custom-authored or imported, starting with Microduck) use standard `UsdPhysics` joint position drives (`joint_target_q` at control rate) rather than hardware-specific servo plugins such as upstream `bam`, and execute `Waypoint` navigation hierarchically: the `Device Program` computes bounded body-frame velocity commands from the current pose and target `Waypoint`, while the trained `Policy` tracks body-frame velocity commands at control rate. `ObservationSpec` exposes a general command vector so end-to-end relative-pose policies remain testable as future RSI `Candidate`s without changing the runtime interface.

We rejected importing `bam` and upstream pre-trained weights because `bam` pins `Python < 3.13`, injects per-substep motor-circuit and 28-joint backlash state into the solver loop—violating the platform's device-agnostic `PhysicsPort` boundary—and ties ONNX inputs to a 61-dim gamepad stream without platform `Job` provenance. Standard position drives keep any new Embodiment's training and `NewtonSimulation` (`cpu` or `cuda`) consistent in pure simulation while deferring hardware-specific actuator identification to a future `real` Embodiment. Hierarchical velocity tracking matches established legged-robotics practice, transferring zero-shot to arbitrary `Scene` packages and new `Waypoint`s without retraining.
