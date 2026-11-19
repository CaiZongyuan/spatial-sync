---
status: accepted
date: 2026-11-19
---

# Store Motion Clips and Asset-pinned Reference Motions separately and export individual Policies

Store the morphology-independent `Motion Clip` and the retargeted `Reference Motion` as separate immutable artifacts so the same source motion can be assessed against several robot `Asset` versions. Share a phase clock and interpolation contract across training, `embodied.policy` and exported inference to keep their motion targets consistent. Package external inference as a single-`Policy` `Policy Export` with its model, inference contract, provenance and reference runner, keeping in-platform deployment authority with the `Controller` promotion workflow.

## Considered Options

- **A single morphology-independent `Reference Motion`** conflates pose extraction with robot-specific retargeting, which depends on a given `Asset` calibration.
- **Minimal ONNX and joint-list exports** omit essential inference and calibration conventions, which can cause inference to diverge or a motor command to reverse.

## Consequences

- Retargeting requires explicit limb and site mapping for the selected `Asset` version. Feasibility checks and a Web ghost preview let the user assess the `Reference Motion` before training.
- `lw.policy-export/1` includes source-joint calibration and requires numeric parity with platform inference, which lets users verify the exported policy before hardware integration. [Motion and export contracts](../spec/LAB_SELF_SERVICE_EMBODIED_SPEC_V1.md#动作与导出契约)
