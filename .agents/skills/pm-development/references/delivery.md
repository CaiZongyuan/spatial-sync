# Delivery and Rework Control

Apply these coordination criteria within the host's implementation, testing, review, and diagnosis methods. User scope, runtime constraints, and host rules take precedence.

## Early Evidence and Stable Candidates

At dispatch, make known verification risks actionable: failure signature, smallest existing entrypoint, relevant preconditions, tried remedies, unknowns, and an exit condition. Separate a fixture's stable capacity workload from a product's real retention behavior. Verify shared runtime assumptions in the first integrated batch while repairs are cheap.

For a high-risk oracle or consequential design, get a bounded non-author check of the first actionable example before expanding the pattern. Inspect the authoritative contract, an independent expected result, and the smallest counterexample that would catch a wrong implementation or reject a legal one. This applies especially to dynamic tolerances, rejection/recovery, ordered results, gaps, time, and rate-limit preconditions. Reuse valid TDD red/green evidence; add a counterexample when the oracle changes or a key risk lacks coverage.

Use existing low-cost formatting, type, static, and focused public-behavior checks during editing. Classify cost by actual execution: a documentation check can invoke a compiler. Run full coverage on the stable candidate in the responsible environment, which may be agreed CI. Independent groups may run concurrently when their actual dependencies and resources permit it; every required group still needs a result.

Record source/test tree, target build, command, non-sensitive environment/dependencies, actual test coverage, exit and cleanup status, and evidence paths using the existing runner's receipts. Identify uncommitted inputs when present. Zero collected tests, skipped target scenarios, broken locators, or compilation failures leave the target behavior unverified.

Reuse coverage by examining the complete candidate, contracts, dependencies, environment, and semantic delta. A different commit alone does not invalidate every check; unchanged source alone does not establish valid runtime evidence. Cite an existing complete gate's affected coverage instead of immediately repeating it. Required final-head CI remains binding.

## Browser Journeys

Use the approved viewport and journey scope. Before reserving execution resources, inspect discovery, identity, target state, component actions, and rendering prerequisites. Dynamic UI readiness follows a business or geometry predicate rather than a fixed sleep or a stale rectangle.

Cover critical paths through the real business journey; use focused scenarios for layout, copy, or selector changes and reuse unaffected evidence. Visibility means the user can see and operate the content. Relevant screenshots plus geometry or hit testing can distinguish an exposed scene from a canvas covered by panels. Keep failed-scenario evidence separate and redact sensitive fields.

## Independent Review and Repair

Pin the base and complete task-owned candidate, including consumers, new files, and uncommitted changes. Follow the host's reviewer policy and explicit reviewer count. Otherwise, one non-author reports Standards and Spec separately; record its reduced context independence. Prefer two independent reviewers for permissions, migrations/transactions, idempotency/recovery, complex budget algorithms, broad shared contracts, and milestone integration. Rebalance capacity first; if still constrained, record actual coverage and remaining risk. Author self-review cannot provide independent coverage.

The review method comes from installed Matt `code-review` or the host's equivalent. For high risk, use the existing reviewers to challenge concrete assumptions and seek counterexamples; another mandatory review round or cross-vendor model is unnecessary. Formal review checks acceptance and related boundaries together. Each defect needs a location, falsifiable basis, and acceptance condition; optional style preferences and large refactors stay separate.

Batch compatible repairs with the original Developer when available. Refresh affected verification and independent review on the complete repaired candidate, reusing unchanged coverage and roles. Repeated rounds with no new evidence, a recurring defect class, or conflicting findings trigger [Advisor consultation](advisor.md). A retry limit prompts reconsideration; it cannot establish a pass. Complete simplification before final validation and formal review; reuse it while its inputs remain valid. Stable-candidate CI can overlap review.

## Bounded Diagnosis

Use Matt `diagnosing-bugs` or the host's equivalent through the original public failure. Preserve the original assertion and observations that distinguish causes. A cause-equivalent reproduction is sufficient; match an exact number only when that number separates competing causes.

Each retry answers an unresolved question by changing a justified condition. Repeated green runs with identical inputs cannot validate a defective performance oracle. Preserve acceptance, budgets, thresholds, and data semantics; diagnostic guards must permit the original assertion to be observed. An instrumentation repair supported by reliable evidence can proceed without exhaustive upstream investigation.

At the diagnosis budget, summarize knowns and unknowns, reconsider the probe, consult, or report the actual blocker. Release heavy resources and continue independent ready work. Missing permissions or external state remain explicit limits.

## Execution Ownership and Records

An execution phase can include targeted red, minimal green, related checks, and cleanup. Reserve enough time for startup and cleanup, and hold heavy locks only for execution and cleanup. Use the host's isolation fixture and ownership ledger. Reconcile global resources at phase start, abnormal recovery, and completion; reconcile owned resources and consumers on ordinary retries. Confirm creating processes and consumers have stopped before removing owned temporary resources. Follow interruption/recovery cleanup and preserve persistent or shared development services.

Reuse existing receipts and stage journals. CI state changes can come from the [observer](ci-observer.md); required-check selection, review coverage, authorization, and delivery remain PM decisions. Record runner completion, result notice, and next action separately when known. Current checkpoint and HTML are views of the same facts; keep unknown intervals unknown rather than attributing elapsed time to waiting or CPU work.
