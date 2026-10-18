# spatial-sync

A local laboratory workbench. A physics source (Newton, or the GPU-free Synthetic source) is the single source of truth. The gateway streams its spatial state to a Three.js viewer and runs a declarative information plane (device types, actions, recordings) that the Web app, the `lw` CLI and `lw mcp` share.

Intent lives in three documents:

- `docs/spec/LAB_INFORMATION_FOUNDATION_SPEC_V1.md`: current scope, milestones (§15) and decisions (§18). Read the sections a task touches before designing.
- `docs/spec/NEWTON_SPATIAL_SYNC_SPEC_V1.2.md`: the spatial-plane baseline that the newer spec amends.
- `docs/VERIFICATION.md`: launch and check commands. Run its mandatory checks before reporting work done.

## Current focus

The user maintains this section; update it when a milestone lands or direction changes.

- Delivered: information spec I0–I4 (simplification, information model, CLI/MCP, Newton host, recording and replay), plus the platform overview and native Newton viewer.
- Open: I5, Claude Code completing the I3 journey on `robot-and-tubes` through MCP, recorded.
- Next: the RSI platform. Start from `docs/spec/LAB_RSI_PLATFORM_PRD_V1.md` (user stories, delivery order, contract changes, test seams, error codes); `docs/spec/LAB_WEB_UI_SPEC_V1.md` holds the design system, frame (W0) and page contracts. Tickets are GitHub sub-issues #3–#46 of PRD issue #2, with native blocking edges.
- Specified, not started: `docs/spec/LAB_ASSETS_AND_SCENES_SPEC_V1.md` (A1–A3: library, scene packages, validation, `fr3-lab` import), `docs/spec/LAB_TRAINING_SPEC_V1.md` (T0–T3: FR3 contact-grasp spike, jobs and Training View, evaluation, policies and the Lab Notebook, agent-written programs and demonstrations), `docs/spec/LAB_LIVE_OPERATIONS_SPEC_V1.md` (L1–L3: sensors, alerts, history and inbox; operation plans with path preview; Live Lab over MQTT) and `docs/spec/LAB_RSI_SPEC_V1.md` (R1–R4: evaluation center and cost center, headless Agent runner and Harness RSI, Embodiment/Controller/Robot Model resources). T0 can start alongside A1; L1 and L2 follow A1. The UI prototype at `apps/web/prototypes/platform-redesign.prototype.html` is a visual reference only; production code follows the Web UI spec.
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

- One agent carries an issue end to end; the user reviews the diff and merges. Use `pm-development`, parallel agents or independent reviewers when the user asks for them.
- Use `grilling` for decisions that touch the contracts above or the device model. Settle everything else with sensible defaults and state them in the reply.
- A milestone is done when it demos: one local command starts it, or it is visible in the browser.
- Evidence lives in the PR description and CI. Repository docs describe current behavior for users and agents; process receipts, review logs and benchmark output go to `.scratch/`.
- Add an abstraction when its second consumer exists.
- The CI gate stays GPU-free and under 5 minutes. Newton smoke tests and benchmarks run manually.

## Repository workflow

These repository choices override automatic workflow defaults in installed skills.

- Create linked worktrees under the primary checkout's `.worktrees/<task>/`. Resolve the primary checkout from `git worktree list`; use its directory even when working inside a linked worktree.
- Keep `.scratch/` and `.worktrees/` local and untracked. Store temporary diagnostics and validation artifacts in `.scratch/`.
- Timeline reports are opt-in. Invoke `development-timeline` and create or update its journals, checkpoints, or HTML reports only when the user explicitly requests a timeline. Invoking `pm-development`, reaching a milestone, or finishing a task does not activate timeline generation. Otherwise, report results in the normal conversation.

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues for `CaiZongyuan/spatial-sync`, managed with `gh`; blocking edges use native issue dependencies and tickets are sub-issues of their spec issue. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
