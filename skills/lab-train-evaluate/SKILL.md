---
name: lab-train-evaluate
description: "Run and monitor approved training, analyze failures, evaluate exact Policy or scripted Device Program material, and request human Controller promotion."
---

# Train, evaluate and request deployment

1. Read the exact current Plan, Goal budget and prior public train results. Confirm
   the Task, fixed Scene/Suite, approved revision, allowed material/code and remaining
   work before starting a Job or Evaluation:

   ```text
   lw plan show plan-example --json
   lw loop show goal/example --json
   lw notebook show goal/example --json
   lw suite show suite/example@1 --json
   lw score ls --suite suite/example@1 --json
   ```

   A changed hypothesis, Task, code snapshot or exhausted budget returns to planning
   for a fresh human-approved revision. Training and deployment are separate grants.

2. For a Policy training round, start only the authorized execution mode, seed and
   exact approved revision. Respect an explicit pause. Reuse the request ID when
   retrying admission; a queued/pending-approval Job has not begun training:

   ```text
   lw job start plan-example --seed 42 --revision 1 --request-id train-example --json
   lw job show job-example --json
   lw node ls --json
   lw job logs job-example --follow --json
   ```

   Monitor actual node heartbeat/lease state, iteration progress, reported reward/
   success/memory and logs. Inspect TensorBoard's returned local URL when its
   installed runtime is available:

   ```text
   lw tensorboard --json
   ```

   If logs/metrics establish OOM, nonfinite/divergent optimization or a mismatch
   with the approved runtime, cancel this Job and retain the actual reason:

   ```text
   lw job cancel job-example --json
   lw job show job-example --json
   ```

   Finish cancellation only when the platform confirms terminal cleanup/lease
   release. A stale heartbeat or missing metric is unknown evidence; preserve
   unconfirmed ownership and read retained lifecycle/logs. Summarize the Job,
   failure, last known metrics and charged usage, then return to
   [lab-training-plan](../lab-training-plan/SKILL.md) with one supported change.
   Hyperparameters, environment count, SDK, seeds and budget stay within approval;
   a changed experiment requires a fresh human-approved Plan.

   Continue after a succeeded Job only when its artifact links expose the final
   checkpoint, ONNX export and `versions.json`. Check exact Task/Scene/seed,
   actual runtime/hardware versions and export/checkpoint/weights hashes against
   that Job. Missing artifacts or mismatched versions return to failure analysis.
   A scripted Device Program creates no training Job: retain its approved exact
   code snapshot and continue directly with material inspection and evaluation.

3. Select immutable material and retain its exact provenance. For a Policy, freeze
   the succeeded Job export, inspect its missing evidence, and verify observations
   using artifacts from that same authenticated Job:

   ```text
   lw policy create job-example/checkpoints/model_0.pt --json
   lw policy show policy/example@1 --json
   lw policy check-observations policy/example@1 --artifact observations/native-samples.json --json
   lw policy verify-observation-source policy/example@1 check-example --artifact observations/native-capture.json --json
   ```

   A numerical pass or legacy capture contract alone leaves qualification unverified.
   Native qualification requires the registered source, exact export boundary and256
   distinct actual trainer states. Controlled fixtures demonstrate engineering only.
   For a Device Program, retain its exact immutable package and approved code Plan;
   use the program commands advertised by this installation's help.

4. Evaluate only the public train split, funded by the current approved Plan. Inspect
   complete results, cleanup and failed recordings through their public IDs:

   ```text
   lw eval run policy/example@1 --suite suite/example@1 --plan plan-example --revision 1 --json
   lw eval show eval-example --json
   ```

   Open the returned public recording links in the Web viewer to inspect failures.

   A valid accepted result must remain the current statistical Reference in its
   fixed comparison group. Acceptance compares its Wilson lower bound with the prior
   Reference's measured score. Unknown, invalid or incomplete evidence returns to
   diagnosis/planning and preserves its actual attempts and budget charges.

5. Request promotion for that exact current accepted Candidate and fixed Scene:

   ```text
   lw policy promote policy/example@1 --scene robot-and-tubes@2 --evaluation eval-example --request-id promotion-example --json
   lw promotion show promotion-example --json
   ```

   Retain the returned promotion ID for retries. The platform runs fresh private
   tests through its shared queue and gives the human a dedicated review. Use only
   public promotion progress; private questions, seeds, results and recordings stay
   outside the Optimizer's context. Human decisions use the existing approval UI.

6. Finish by reporting the exact material/train/promotion IDs, current public status,
   remaining gaps and next permitted action. A published Controller applies to new
   Sessions through its derived Scene; an already active Session retains its loaded
   identity. Report genuine training/contact quality only when actual evidence proves it.
