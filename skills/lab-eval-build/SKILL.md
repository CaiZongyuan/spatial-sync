---
name: lab-eval-build
description: "Build and publish a fixed Agent evaluation Suite from an explicitly selected local package; check public coverage and establish a measured Reference."
---

# Build an Agent evaluation Suite

The Builder owns private statements and grading files. Keep its directory and
controller credential outside Optimizer and Target conversations. External
runtimes execute their own model calls under the user's model and cost authority.

1. Read the target Agent and Scene. Select a local Suite package with explicit
   `layer: agent`, `target`, exact Scene/content hash, `source`, `runsPerCase`
   (at least three), and cases classified as train or test. Each case names one
   statement under `statements/` and one grading YAML under `private/`.

   ```text
   lw agent show lab-operator --json
   lw scene show lab-demo@3 --json
   lw suite push <selected-suite-directory> --json
   ```

   Retain the returned immutable Suite ID and content hash. Publication validates
   the selected bytes, Scene and Agent. Public reads include train statements and
   grading dimension names; they expose no held-out statements or grading rules.
   Re-publishing identical bytes returns the same version.

2. Check public coverage and exact Harness composition before evaluation:

   ```text
   lw suite show suite/example@1 --json
   lw agent harness lab-operator 1 --json
   lw agent-run create --help
   ```

   Use `lab-agent-evaluate` for a bounded verification and then a complete train
   run with the declared runtime/model/thinking context. Every repeat uses a fresh
   Target and the returned case connection. Partial, invalid or verification
   evidence cannot establish the formal Reference.

3. Establish the measured Reference from the exact complete train evaluation:

   ```text
   lw agent baseline lab-operator --evaluation <evaluation-id> --request-id <stable-request-id> --json
   lw agent rsi lab-operator --json
   ```

   Completion means the platform retained every required case, verified evidence,
   and supplied known environment/judge identities for the current Reference
   Harness. A missing identity stays unavailable; changing local metadata or
   supplying a caller score cannot replace evaluation.

Held-out work starts only after an accepted training round requests promotion.
Its separate controller binds a fresh native conversation before receiving the
private statement or Target connection. Human review owns test results and the
Reference decision; Builder publication never grants that decision to a model.
