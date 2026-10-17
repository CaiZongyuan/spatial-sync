# Repository workflow

These repository choices override automatic workflow defaults in installed skills.

- Create linked worktrees under the primary checkout's `.worktrees/<task>/`. Resolve the primary checkout from `git worktree list`; use its directory even when working inside a linked worktree.
- Keep `.scratch/` and `.worktrees/` local and untracked. Store temporary diagnostics and validation artifacts in `.scratch/`.
- Timeline reports are opt-in. Invoke `development-timeline` and create or update its journals, checkpoints, or HTML reports only when the user explicitly requests a timeline. Invoking `pm-development`, reaching a milestone, or finishing a task does not activate timeline generation. Otherwise, report results in the normal conversation.
