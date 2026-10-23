# Delivery

Rules for merging, PM progress and hand-off, and diagnosis. They override the defaults in installed skills such as `pm-development` and `code-review`.

## Merging

- Push the branch, open the PR and merge it without asking once non-author `code-review` finds no open defects on the final candidate (a judgement call may stay open with its reason in the PR) and the rules below hold. A PR that changes a contract in `AGENTS.md` also needs the user's approval of that change, recorded in its issue or PR. When the user withholds merging, stop at a passing PR.
- Merge only while the latest main push CI is successful. When it is red, repair it before merging other PRs; if a repair is not ready within one hour, revert the introducing squash commit.
- Rebase the PR head onto current main before merging; that exact head must pass CI. Verify the integrated tree equals the reviewed and tested candidate.
- Browser tests retry once in CI. A pass after a retry counts as a pass; record its flaky cases in the flaky-test issue and fix them in a weekly batch instead of blocking merges. A case that flakes three times in a week goes to the front of the maintenance queue.
- Newton runtime changes need the four Newton CPU checks in `docs/VERIFICATION.md` ("Real physics and browser checks").
- Start a regression-fix PR description with `Regression from #N`, naming the introducing PR.
- Use the `pr-media` branch only for PR screenshots and recordings.

## Progress and hand-off

- Post progress to #2 after each merged batch and at least every 12 active hours: merged PR → issue, active issue/branch/next step, paused work/reason/resume condition, main CI, and user actions. Comment on a ticket when starting or pausing it.
- Status lives in issues. A milestone PR updates its issue and #2, and leaves `AGENTS.md` unchanged unless a durable rule changes.
- Budgets and authorizations for one ticket (for example T0's GPU budget) live in that ticket.
- Hand off PM work in one new GitHub issue titled `[PM交接] …` and linked from #2: current state, plan and resume steps in its body, evidence as links to issues, PRs and branches. Push unpublished candidate branches and reference specs by their repository paths.

## Diagnosis and experiments

- Local CPU work is free: tests, offline IK/FK, static geometry, Synthetic and Newton CPU runs, and local profiling need no authorization token, method review or execution window. Iterate and retry as needed.
- Approval is needed only for GPU training, real devices, publishing releases or assets, and contract changes. Only GPU and benchmark runs that need an idle machine hold the machine lock.
- A failure caused by tooling or the environment (launcher, environment variable, file lock, port) is not a result. Fix it and rerun; it consumes no budget.
- Budgets are time boxes per phase in working hours, not counts of runs. Give each diagnosis issue a one-day box. At its end post a conclusion, a new direction, or a pause with a resume condition. After two boxes without convergence, write the options with a recommendation and label the issue `ready-for-human`.
- A progress comment states what was tried, the key numbers, the conclusion and the next step in about 15 lines. Hashes, PIDs and receipts stay in `.scratch/`.
- Mechanical and layout choices inside an approved milestone are PM decisions.
- Run numerical and static diagnostics on Linux/WSL, the documented baseline.
