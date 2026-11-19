---
status: accepted
date: 2026-11-19
---

# Separate source Motion Clips from Asset-pinned Reference Motions and export Policy-level bundles

Video-to-robot imitation separates two distinct immutable artifacts: a morphology-independent **`Motion Clip`** (3D human pose sequence extracted from a single-subject video by a `Compute Node` preprocessing `Job`, retaining upstream body-model license notices such as SMPL terms) and an `Asset`-version-pinned **`Reference Motion`** produced by retargeting a `Motion Clip` (or imported keyframe sequence) onto a specific robot `Asset` revision through an explicit anatomical body/site mapping. Retargeting supports an optional in-place root mode to suppress monocular camera-drift translation, rejects motions whose required active limbs have no mapped robot degrees of freedom, and records kinematic feasibility metrics (keypoint error, foot-ground slip, joint-limit saturation, angular-velocity bounds and static support-polygon margin) in its `Validation Report` alongside Web 3D ghost preview before spending RL GPU budget. Phase clock (`sin/cos` for cyclic clips, remaining-time plus terminal hold for one-shot clips) and `slerp`/joint interpolation rules are shared via a single specification across training, `embodied.policy` and external export.

For external and future Sim2Real use, the platform exports **`Policy Export`** archives (`lw.policy-export/1`, scoped to a single `Policy` rather than a full `Controller` to avoid conflation with in-platform `deployment_authority` permits). Each archive bundles the static `.onnx` model (with baked observation normalization), any required `Reference Motion` slice data, propagated asset/motion license notices, qualification/evaluation status badges, a complete `manifest.json`—specifying per-term quaternion order (`xyzw`), `projected_gravity` sign and angular-velocity frame, raw unclipped `previous_action` reset/step semantics, `offset`/`delta` `maxDelta` and action/physical limit clipping order, control `hz`/`physics_dt`/decimation, actuator `kp`/`kv`/effort/armature in SI units, calibrated base-link transform, and the **source URDF/MJCF joint name, axis-sign and zero-offset table**—plus a zero-platform-dependency reference Python runner verified against platform inference within `float32` tolerance (`1e-5`).

## Considered Options

- **A single "morphology-agnostic" `Reference Motion` type**: conflates raw human pose extraction with robot-specific joint retargeting that must invalidate whenever `Asset` calibration changes.
- **Minimal `.onnx` + joint-list exports**: omits quaternion ordering, axis flips from URDF normalization and `previous_action` semantics, causing silent divergence or reversed motor motion on real hardware.
