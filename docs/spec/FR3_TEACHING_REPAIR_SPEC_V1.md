# FR3 offline teaching repair

## Scope

Repair the offline teaching failure reported by #18. The user requested this
repair in a separate worktree on 2026-10-22, following the proposal to distinguish
teaching, physical P6 and complete motion acceptance. This is a new repair phase;
the two failed mechanical feasibility windows remain consumed and unchanged.

The platform continues to execute taught joint configurations. No runtime IK,
new device action, protocol change or Blender conversion script is introduced.
The original FR3 calibration, six tubes, four centrifuge slots and published
scene versions remain unchanged. The scene factory stays outside the platform.

## Teaching requirements

- Preserve the existing strict numerical acceptance by default: the norm of
  position error in metres and rotation-vector error multiplied by 0.2 must be
  at most 0.0001. A numerical failure reports residuals and joint-limit margins;
  failure of a bounded local solver is not a proof of global unreachability.
- Diagnostic continuation may distinguish a local-solver failure from a
  joint-boundary obstruction on one branch. Such intermediate targets are a
  numerical diagnostic, not runtime waypoints or collision evidence. The tool
  itself continues to use one bounded solve with at most 400 evaluations.
- An approach height is a scene teaching input. Its default remains 150 mm;
  changing it must be explicit and recorded. It must be finite and positive.
  Actual tube extraction, neighbouring tubes, hand clearance and the executed
  joint-space motion still need physical validation.
- Preserve the selected scene's exact version, content hash, pinned physical
  TCP, base pose, grasp height, original named locations and grasp orientation.
  A failed regeneration must not publish partial locations or overwrite a
  previous output.
- Physical P6 stays at 10 mm for the calibrated TCP at each grasp. Numerical
  teaching does not mark P6, path/contact, settling, load or A3 acceptance passed.

## Bounded repair validation

The initial diagnostic/regression phase uses CPU-only URDF FK/IK, with at most
15 minutes total numerical execution and no randomized search. First reproduce
the saved B3 failure using the actual Teacher and its recorded inputs. Compare
one fixed 10 mm continuation sequence on that target, then check all original
locations with an explicit 100 mm approach candidate. This changes only the
approach height; the original target orientations and strict tolerance remain.
The lower height gives the observed B3 branch more joint-limit margin; it does
not establish physical extraction clearance. Record each change and result
separately. No automatic height or orientation search is introduced.

This initial phase does not run another full mechanical feasibility experiment.
Before Newton integration, publish the final numerical candidate, exact changes,
physical checks and a finite execution allocation. The old 2/2 ledger cannot be
reset or its unused time reused. Keep original failed source/data immutable.

## Delivery

Regression coverage must exercise the real teaching entrypoint, preserve legacy
defaults and reject invalid heights and partial output. Run repository mandatory
checks and independent Standards/Spec review. A repaired teaching tool does not
close #18: imported scene launch, PBR, full CLI I3 and agent discovery remain A3
requirements in `LAB_ASSETS_AND_SCENES_SPEC_V1.md`.
