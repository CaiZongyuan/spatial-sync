---
name: change-impact
description: Assess changes to shared APIs, schemas, dependencies, or resource ownership by tracing real consumers and testing the facts required for compatibility and safety.
---

# Change Impact

Turn “this change is safe because…” into a checkable claim. Use this assessment independently or within the existing Developer/Reviewer task. Reuse valid evidence; the assessment itself adds no merge gate.

## Bound the Candidate

Identify the task, authoritative contracts, baseline, and complete task-owned candidate. Inspect committed changes, staged/unstaged edits, and new files; separate unrelated user work. A branch-to-branch diff alone can omit the proposed change. When ownership or the base is unclear, investigate it before claiming complete coverage.

Use existing repository terminology and architectural decisions. Keep the investigation proportional to the changed boundary.

## Trace and Prove

1. Find the actual callers/readers/writers and entrypoints across the changed contract: exports, schemas, adapters, generated registrations, examples, tests, and external consumers supported by available evidence. Follow relevant references and runtime registration; a text-search miss alone cannot prove no consumers exist.
2. For each materially affected boundary, state what behavior changes, which consumer relies on it, and the fact required to preserve acceptance or compatibility. Include shared ports, quotas, data, creating processes, and cleanup consumers for ownership changes.
3. Choose the smallest evidence that distinguishes a safe change from the concrete failure. Reuse public behavior tests, independent expected values, contract checks, representative consumer execution, or current runtime receipts. Static inspection can support static claims; runtime claims need runtime evidence. Label consumers outside available access as unknown.
4. Report uncovered assumptions and the next discriminating check. A missing proof can motivate a narrower change or additional focused verification within the authorized scope.

Example: renaming an event field is safe only if supported producers and readers agree. Trace serialization, browser decoding, retained fixtures, and any compatibility promise; exercise a representative producer-to-reader path. A green producer unit test alone leaves decoding unproved.

## Result

Return a concise impact note with:

- Baseline/candidate identity and task-owned scope, including new/uncommitted files.
- Changed boundary → real consumers → affected behavior.
- Key safety/compatibility facts → evidence and its valid inputs.
- Unknowns, residual risk, and necessary next checks; distinguish optional improvements.

Attach it to the existing issue, review, or evidence record. On a repair or baseline advance, refresh affected claims from the semantic delta instead of repeating the whole investigation. The note supplies evidence for review and PR explanation; completion remains the host workflow's decision.

Method adapted from [pstack blast-radius at df581122](https://github.com/cursor/plugins/blob/df581122cde17e6e27686b5a448bde23e4ad4318/pstack/skills/blast-radius/SKILL.md).
