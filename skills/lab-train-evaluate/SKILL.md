---
name: lab-train-evaluate
description: "Evaluate an exact Policy or scripted Device Program under its approved Plan, inspect public train evidence, and request human Controller promotion."
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

2. Select immutable material and retain its exact provenance. For a Policy, freeze
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

3. Evaluate only the public train split, funded by the current approved Plan. Inspect
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

4. Request promotion for that exact current accepted Candidate and fixed Scene:

   ```text
   lw policy promote policy/example@1 --scene robot-and-tubes@2 --evaluation eval-example --request-id promotion-example --json
   lw promotion show promotion-example --json
   ```

   Retain the returned promotion ID for retries. The platform runs fresh private
   tests through its shared queue and gives the human a dedicated review. Use only
   public promotion progress; private questions, seeds, results and recordings stay
   outside the Optimizer's context. Human decisions use the existing approval UI.

5. Finish by reporting the exact material/train/promotion IDs, current public status,
   remaining gaps and next permitted action. A published Controller applies to new
   Sessions through its derived Scene; an already active Session retains its loaded
   identity. Report genuine training/contact quality only when actual evidence proves it.
