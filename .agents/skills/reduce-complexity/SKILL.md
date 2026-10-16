---
name: reduce-complexity
description: Simplify completed issue or PR changes while preserving behavior, before final validation and independent review. At epic completion, survey the integrated work for evidence-backed reductions in maintenance cost.
---

# Reduce Complexity

Apply a bounded simplification pass to completed changes, or survey an integrated epic for removable maintenance. Follow the host repository's `AGENTS.md`, specifications, accepted decisions, and validation requirements.

## Choose the Scope

- **Issue or PR completion:** follow [local simplification](references/local-simplification.md). Apply small improvements that preserve behavior within the authorized implementation, then run affected checks and obtain independent review against project standards and the specification.
- **Epic completion:** follow [the epic survey](references/epic-simplification.md). Inspect the integrated work and its consumers, then report proposals supported by evidence. Implement only candidates already covered by the user's authorization.

Infer the scope from the active issue or epic; no mode argument is required. An explicit review-only request remains read-only. A survey does not authorize publishing issues, merging, or changing task or specification state. Follow the host repository's tracking conventions and the user's existing authorization for those actions.

## Establish the Evidence

Read applicable `AGENTS.md` instructions, the issue or specification or agreed request, relevant accepted design and domain decisions, and the project's validation requirements before judging implementation or architecture choices.

Use the task's fixed starting point or verified PR merge base, recording the resolved revision and scoped paths. Inspect `git status --short`, `git diff <base-sha> -- <task-paths>`, and `git ls-files --others --exclude-standard` so committed, staged, unstaged, and task-owned new files are considered. Read relevant new files explicitly. Preserve unrelated files and hunks. Resolve a missing base from the task or PR context; ask only if the intended scope remains ambiguous.

For an epic, use the entire integrated range or child-PR inventory described in the epic reference. Zero search matches alone do not establish that a public or dynamically registered capability is unused.

## Finish the Pass

Report the inspected scope, meaningful simplifications or proposals, retained obligations, and evidence gaps. No change is a valid result; there is no deletion quota. After edits, run affected checks and obtain independent review under the host repository's process. Optional cleanup does not block delivery by itself.

Record the inspected revision and any uncommitted scope. Reuse the pass before merge if its inputs remain unchanged; inspect new changes and refresh affected tests and review if fixes or integration changed the result. Do not repeat cleanup merely because a commit or push follows.
