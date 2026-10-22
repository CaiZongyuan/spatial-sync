# spatial-sync

A local laboratory workbench. A physics source (Newton, or the GPU-free Synthetic source) is the single source of truth. The gateway streams its spatial state to a Three.js viewer and runs a declarative information plane (device types, actions, recordings) that the Web app, the `lw` CLI and `lw mcp` share.

Intent lives in three documents:

- `docs/spec/LAB_INFORMATION_FOUNDATION_SPEC_V1.md`: current scope, milestones (§15) and decisions (§18). Read the sections a task touches before designing.
- `docs/spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md`: the spatial-plane baseline that the newer spec amends.
- `docs/VERIFICATION.md`: launch and check commands. Run its mandatory checks before reporting work done.

## Current focus

The PM updates this section in the PR that completes a milestone; the user may override it.

- Delivered: information spec I0–I5, the native Newton viewer, RSI W0/A1/A2, A3 USD loading and Dressing (#17), L1 sensors, alerts, history, agent inbox and simulation telemetry (#19–#23), L2 operation plans, motion preview, plan page and session panel (#24–#26), L3 Device Host/MQTT, continuous recording, history/inbox/alerts/UI and Live plans (#27–#28), and embodied evaluation of fixed suites with session requests and scoreboard (#34). `/live` shows real devices; simulation devices remain in the session's device view. Live operations still has gaps, including `watch --until`.
- Paused: A3 `fr3-lab` import (#18), with its grasp-height prerequisite delivered. Both approved mechanical feasibility windows failed (2/2 consumed); physical feasibility remains unproven and the next step is a scoped proposal.
- Paused: T0 (#29) is frozen; GPU verification has not resumed. Before its second round, publish measurable hypotheses, budget and the unchanged fixed-home audit. Diagnostics have at most 2 GPU hours; at most one full training has 4 hours. The first round remains 0/1,024, below 922. Stop after a second failure and submit a T2 specification proposal labelled `ready-for-human` for the user to decide.
- Paused: centrifuge tube-location transients remain unresolved ([#99](https://github.com/CaiZongyuan/spatial-sync/issues/99)). Resume from a new design that distinguishes the remaining explanations.
- Paused: #85 CI margin validation remains 0/5 accepted attempts; its five-run acceptance has not resumed.
- Next: T1 starts with #30 after A3 (#18), independently of T0. T0 blocks #35 (policies and `robot_arm.policy`), not T1. Use `docs/spec/LAB_RSI_PLATFORM_PRD_V1.md` for stories, delivery order, contracts, test seams and errors; `docs/spec/LAB_WEB_UI_SPEC_V1.md` holds page contracts. Tickets #3–#46 are native sub-issues of #2.
- Follow-on specifications: `docs/spec/LAB_ASSETS_AND_SCENES_SPEC_V1.md` (A2–A3), `docs/spec/LAB_TRAINING_SPEC_V1.md` (T1–T3), `docs/spec/LAB_LIVE_OPERATIONS_SPEC_V1.md` (L1–L3), and `docs/spec/LAB_RSI_SPEC_V1.md` (R1–R4). The UI prototype at `apps/web/prototypes/platform-redesign.prototype.html` is a visual reference only; production code follows the Web UI spec.
- Vocabulary lives in `GLOSSARY.md`; architecture decisions in `docs/adr/`.

Scope comes from a spec milestone or an explicit user request. When a task reaches beyond both, confirm with the user before building; a proposed direction gets a spec first.

## Contracts

These are hard to reverse. Change them deliberately, with the user.

- The protocol and the recording format are public. A change bumps the version and keeps old versions readable; every published recording must still replay.
- `entityKey` is the one identity shared by the spatial and information planes, derived from Newton labels (USD scenes: Prim Paths).
- Protocol geometry keeps Newton conventions: Z-up, half extents, capsule/cylinder/cone along local Z. Only `apps/web/src/lib/geometry/primitives.ts` converts to Three.js.
- Web, CLI and MCP reach devices only through the HTTP API's single action pipeline.
- Device behavior lives in `devices/` YAML and `packages/programs/`. The pipeline in `packages/info` and the API stay device-agnostic, and MCP tools stay generic.

## Working style

- One agent carries an issue end to end. Use `pm-development` or parallel agents when requested.
- Use `grilling` for decisions that touch the contracts above or the device model. Settle everything else with sensible defaults and state them in the reply.
- A milestone is done when it demos: one local command starts it, or it is visible in the browser.
- Evidence lives in the PR description and CI. Repository docs describe current behavior for users and agents; process receipts, review logs and benchmark output go to `.scratch/`.
- Add an abstraction when its second consumer exists.
- The CI gate stays GPU-free and under 5 minutes. Newton smoke tests and benchmarks run manually.
- Push the branch, open the PR and merge it without asking once non-author `code-review` finds no open defects on the final candidate (a judgement call may stay open with its reason in the PR) and the merge rules that follow hold. A PR that changes a contract above also needs the user's approval of that change, recorded in its issue or PR. When the user withholds merging, stop at a passing PR.
- Merge only while the latest main push CI is successful. When it is red, repair it before merging other PRs; if a repair is not ready within one hour, revert the introducing squash commit.
- Before merging a PR that changes `packages/sim_runtime/**`, `packages/programs/**`, `library/scenes/**`, or recaptures a Newton preview under `assets/scene-previews/**`, run all four Newton CPU checks in `docs/VERIFICATION.md` under "Real physics and browser checks". Add a `Newton CPU` paragraph to the PR with commands, results and tick P95. Performance budgets use the documented Linux baseline.
- Rebase the PR head onto current main before merging; that exact head must pass CI. Verify the integrated tree equals the reviewed and tested candidate.
- Start a regression-fix PR description with `Regression from #N`, naming the introducing PR.
- Post progress to #2 after each merged batch and at least every 12 active hours: merged PR → issue, active issue/branch/next step, paused work/reason/resume condition, main CI, and user actions. Comment on a ticket when starting or pausing it. Keep conclusions and key numbers in issues/PRs; raw receipts stay in `.scratch/`.
- Bind process cleanup to this task's current process handle, or verify its PID, command and creation time against the current ownership receipt before stopping it.

## Repository workflow

These repository choices override automatic workflow defaults in installed skills.

- Create linked worktrees under the primary checkout's `.worktrees/<task>/`. Resolve the primary checkout from `git worktree list`; use its directory even when working inside a linked worktree.
- Keep `.scratch/` and `.worktrees/` local and untracked. Store temporary diagnostics and validation artifacts in `.scratch/`.
- Hand off PM work in one new GitHub issue linked from #2: current state, plan and resume steps in its body, evidence as links to issues, PRs and branches. Push unpublished candidate branches, reference specs and `AGENTS.md` by their repository paths, and use the `pr-media` branch only for PR screenshots and recordings.
- Timeline reports are opt-in. Invoke `development-timeline` and create or update its journals, checkpoints, or HTML reports only when the user explicitly requests a timeline. Invoking `pm-development`, reaching a milestone, or finishing a task does not activate timeline generation. Otherwise, report results in the normal conversation.

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues for `CaiZongyuan/spatial-sync`, managed with `gh`; blocking edges use native issue dependencies and tickets are sub-issues of their spec issue. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
