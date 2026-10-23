# spatial-sync

A local laboratory workbench. A physics source (Newton, or the GPU-free Synthetic source) is the single source of truth. The gateway streams its spatial state to a Three.js viewer and runs a declarative information plane (device types, actions, recordings) that the Web app, the `lw` CLI and `lw mcp` share.

## Where things live

- Scope and delivery order: `docs/spec/LAB_RSI_PLATFORM_PRD_V1.md`. Read the sections a task touches before designing.
- Status and next steps: the latest progress comment on #2 and the newest open `[PM交接]` issue. Read them before picking up work.
- Area specs: `docs/spec/LAB_ASSETS_AND_SCENES_SPEC_V1.md` (A), `LAB_TRAINING_SPEC_V1.md` (T), `LAB_LIVE_OPERATIONS_SPEC_V1.md` (L), `LAB_RSI_SPEC_V1.md` (R) and `LAB_WEB_UI_SPEC_V1.md` (page contracts), built on `LAB_INFORMATION_FOUNDATION_SPEC_V1.md` and `NEWTON_SPATIAL_SYNC_SPEC_V1.2.md`. `apps/web/prototypes/platform-redesign.prototype.html` is a visual reference only.
- Launch and checks: `docs/VERIFICATION.md`. Run its mandatory checks before reporting work done.
- Vocabulary: `GLOSSARY.md`. Architecture decisions: `docs/adr/`.

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
- Conclusions and key numbers go in issues, PR descriptions and CI. Raw receipts, review logs and benchmark output go to `.scratch/`. Repository docs describe current behavior.
- Add an abstraction when its second consumer exists.
- The CI gate stays GPU-free and under 5 minutes. Newton smoke tests and benchmarks run manually.
- Stop only processes your task started, through the handle that started them; leave other sessions and services running.
- Before opening or merging a PR, handing off PM work, or running a diagnosis or experiment, read `docs/agents/delivery.md`.

## Repository workflow

These repository choices override workflow defaults in installed skills.

- Create linked worktrees under the primary checkout's `.worktrees/<task>/`; resolve the primary checkout from `git worktree list`. Keep `.scratch/` and `.worktrees/` local and untracked.
- Use `development-timeline` only when the user asks for a timeline.

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues for `CaiZongyuan/spatial-sync`, managed with `gh`; blocking edges use native issue dependencies and tickets are sub-issues of their spec issue. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Image generation

Generate images with the Codex `imagegen` CLI and the provider in `~/.codex/config.toml`. Read `docs/agents/image-generation.md` before the first image.
