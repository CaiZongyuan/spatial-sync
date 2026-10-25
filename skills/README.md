# Product platform skills

`lab` is the platform entry point and explains workflow selection and the RSI
loop. `lab-operate` observes the actual Catalog and state, invokes generic
actions, corrects refusals and verifies outcomes. `lab-library` discovers and
imports assets/scenes, reads completeness and validation, and revalidates exact
immutable versions. `lab-training-plan` interviews the user, snapshots a Task
and submits measurable hypotheses for human approval. `lab-runtime-records`
imports existing Claude Code captures, refreshes their messages and reported
usage, and reads normalized evidence. `lab-agent-evaluate` coordinates public
cases with an external runtime and reads platform-owned grades. `lab-scene-director`
turns an external scene request into trusted asset selection, acquisition, Blender
authoring, exact-version import/validation and received Authoring Reports. All
skills reserve approvals and
Vault writes for a person, including when a user asks an agent to bypass them.

These are shipped product files. Repository development skills remain in
`.agents/skills/` and are managed separately.

```text
lw skills ls --json
lw skills install --target "/path/to/agent skills" --json
```

`--target` is the parent skill directory: the installer creates the shipped
skill directories (`lab`, `lab-agent-evaluate`, `lab-eval-build`,
`lab-harness-optimize`, `lab-library`, `lab-operate`, `lab-runtime-records`,
`lab-scene-director`, `lab-train-evaluate` and `lab-training-plan`) beneath it.
Quoted paths containing spaces work.
The default is `~/.claude/skills` (Claude Code's user skill directory); choose
another agent's directory explicitly with `--target`.

Installation is local and works without a running gateway. Repeating the command
keeps identical files and their modification times. Different existing content, a file
where a skill directory belongs, or a symlink/reparse point is a conflict. Known
conflicts are checked before any skill is installed. Existing user files and
additional files inside an identical installed skill are preserved. Choose a
different target or review a conflict yourself; the installer has no overwrite
or force option. Publishing requires a filesystem with hardlink support; an
unsupported target fails safely without replacing existing files.

Skills are included in the platform wheel under its environment's
`share/spatial-sync/skills` directory. Editable checkouts use this canonical
source directory. The installed files use UTF-8 with normalized newlines.

For development, run `python tools/check_platform_skills.py`. It reads every
command in the product `SKILL.md` files and checks the real CLI parser, including
subcommands and arguments. Examples belong in inline code or as one complete
invocation per code-block line; angle-bracket arguments are templates. The
checker parses examples without executing them or contacting a laboratory.
CI runs it alongside tests that deliberately misspell a real subcommand.
