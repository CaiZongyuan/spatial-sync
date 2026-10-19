# FR3 contact-grasp experiment

Manual T0 experiment for issue #29. It uses Isaac Lab 3.0 kitlessly with Newton
MJWarp, the repository's actual FR3 URDF, a free 16 mm × 30 mm tube, and a
primitive guide rack. The SO-101 tutorial supplies the PPO configuration,
bounded Gaussian policy head, geometry helpers, and contact solver settings.
Adapted tutorial code retains its Apache 2.0 license in this directory.

This experiment is outside the platform runtime. It has no accepted training
or 1,024-episode audit result yet. A successful scripted trajectory or reset
generation does not establish policy success or change decision D-01.

## Windows setup and commands

Run from the repository root with `uv` available. Setup creates an isolated
Python 3.12 environment under this directory and converts the FR3 asset with
the official standalone URDF importer. It preserves the existing Isaac Lab
and IsaacLabTutorial environments.

```powershell
./experiments/fr3_contact/run.ps1 -Stage Setup
./experiments/fr3_contact/run.ps1 -Stage Probe
./experiments/fr3_contact/run.ps1 -Stage Generate
./experiments/fr3_contact/run.ps1 -Stage Train
./experiments/fr3_contact/run.ps1 -Stage Audit -Checkpoint '<absolute model path>'
```

Every stage sets `PXR_WORK_THREAD_LIMIT=1`, UTF-8 output, and the task import
path. Local USDs, reset datasets, and training logs go under the primary
checkout's `.scratch/rsi-platform/issue-29/`; `-Evidence` selects another
owned directory. Generated files are never committed. Setup and generation
refuse to overwrite existing outputs; use a fresh evidence directory for a
new asset or dataset candidate. Do not run Setup while this environment serves
another process.

Probe stops after the physical lift stage and prints measured poses and jaw
forces. Its exit status indicates completion of the diagnostic, not T0
acceptance. Generate must collect 128 connected physical trajectories before
writing 1,024 balanced reset rows, one per phase per trajectory. An incomplete
batch fails without writing a dataset. No weld or object pose write moves the
tube during the trajectory.

Train uses 2,048 environments, seed 42, and 800 iterations. RSL-RL writes
TensorBoard events and checkpoints every 50 iterations under the evidence
directory. Resume with `-Stage Train -Checkpoint '<absolute checkpoint path>'`.
Capture actual iteration times, whole-board NVIDIA memory samples, and total
wall time for the training report; the experiment makes no premeasured FR3
performance claim. Run one owned GPU job at a time.

Audit uses an explicit checkpoint and exactly 1,024 environments. It repeats
128 validated open-gripper home starts eight times, counts each environment's
first episode once, and prints `FR3_EVAL_RESULT`. `success_rate` and
`contact_grasp_success_rate` require the complete observed contact-grasp path;
`raw_placement_rate` is a separate diagnostic. T0 requires at least 922 contact
grasp successes out of 1,024. Training from later curriculum phases cannot
satisfy the home-only audit.

RSL-RL 5.4.1 playback calls the policy with deterministic mean actions by default.
The launcher leaves Isaac Lab's additional `--deterministic` seed/framework mode
unset; that setting is separate from action selection. Record the actual action
selection and simulation mode alongside every audit result.

## Physical success and reset history

All quantities are SI, Z-up, with XYZW quaternions. The tube root is its center.
The tabletop is at world Z = 0.020 m, so an upright resting tube center is at
0.035 m. The rack root is at 0.020 m; seated center bounds are rack-local
Z = [0.013, 0.019] m and X/Y = ±0.005 m. A tube resting on the guide rim is
outside these height bounds.

The task requires these facts in order:

1. Both jaws contact the tube above 0.05 N for two consecutive samples while
   its center is more than 6 mm above its source resting height.
2. While still held, its lowest point clears the rack by the configured margin.
3. Its upright tip enters the target opening after the proved lift.
4. The released tube stays upright and within the seated bounds for ten
   consecutive 30 Hz samples, with linear speed below 0.06 m/s and angular
   speed below 0.8 rad/s.

Pure sliding placement is excluded from contact-grasp success. The held-goal
reward requires current load-bearing jaw contact, so a dropped tube does not
continue collecting that shaping reward.

Training resets restore demonstrated history from later phases without paying
their milestone rewards again. Audit resets select only the home phase,
validate its physical start geometry, clear all prior history, and reject any
counted episode with seeded grasp/lift history. Loaded contact states still
require a physical replay check before a new reset dataset is used for training.

## CPU contract checks

```powershell
$env:PYTHONPATH = (Resolve-Path ./experiments).Path
uv run --no-sync --project ./experiments/fr3_contact pytest -q ./experiments/fr3_contact/test_contract.py
uv run --no-sync --project ./experiments/fr3_contact ruff check ./experiments/fr3_contact
```

These check the success oracle, ordered history, reset admissibility, and exact
audit accounting. They do not replace the manual GPU checks or policy audit.
