---
name: lab-harness-optimize
description: "Improve an Agent Harness through one bounded staged change, a human-approved Agent Plan, shared train evaluation and verified keep/restore decisions."
---

# Optimize an Agent Harness

Use an authenticated remote gateway. The controller gives Optimizer a fresh
native conversation and an `lw-rsi-opt-` capability as `LW_TOKEN`, with only
`lw mcp` as its platform interface. This automatically selects the restricted
Optimizer tool set. Keep controller tokens, private Suite directories, Shell,
other file tools and private-controller MCP outside that conversation.

1. Read the measured current Reference, exact files and previous public evidence:

   ```text
   lw agent rsi lab-operator --json
   lw agent harness lab-operator 1 --json
   lw notebook show goal/example --json
   lw loop show goal/example --json
   ```

   Retain the exact Suite, Reference evaluation/version/hash and declared runtime,
   model and thinking context. If no complete measured Reference exists, return
   to the controller's `lab-eval-build` workflow. Missing comparison facts block
   admission; imported claims are not a baseline.

2. Propose one behavior paragraph in AGENTS.md, one Agent-owned skill, or one
   permitted configuration field. Pass the complete confined text file list,
   `referenceVersion`, stable `requestId` and one `change` description to
   `agent_candidate` (CLI: a selected local YAML request). Staging retains a new
   immutable version and leaves the editable Harness untouched.

   ```text
   lw agent candidate lab-operator <proposal.yaml> --json
   lw plan submit <agent-plan.yaml> --json
   ```

   The Plan has `kind: training`, `layer: agent`, explicit Goal, Agent, immutable
   Suite, Reference and candidate versions/evaluation, fixed runtime declaration,
   one change, hypotheses, Notebook reads, rationale/interview, finite case/wall
   budget and the original Goal budget. The CLI reads the interview Markdown file
   relative to the Plan; MCP takes its text in the structured Plan. A requested
   finite `budgetException` needs an explicit reason and a human approval note.

3. Wait for the exact revision's human approval. Optimizer cannot approve itself.
   Then activate and dispatch through the shared evaluator:

   ```text
   lw agent-round start <plan-id> --revision 1 --request-id <stable-round-request> --json
   lw agent-round show <plan-id> --json
   ```

   The external controller uses `lab-agent-evaluate` to run fresh public Targets.
   Optimizer reads public results; it never receives Target/controller tokens.
   An editor conflict preserves the person's bytes and dispatches no new Source.
   Retrying a historical activation does not overwrite a later edit.

4. Settle from complete platform evidence:

   ```text
   lw agent-round settle <plan-id> --revision 1 --json
   lw agent versions lab-operator --json
   lw agent tool-trace lab-operator --goal goal/example --json
   ```

   A strictly higher complete train mean retains the candidate; another valid
   score restores the actual Reference files. Agent scores use 0–100, observed
   min/max and at least three repeats, without a confidence interval. Rejected
   snapshots remain available. Inspect application/cleanup blocks before moving
   on. Native cost snapshots retain their basis/time; later imported high-water
   totals may grow. Unknown usage stays unknown. Observed spending stops future
   admissions without promising a pre-call hard USD ceiling.

5. After acceptance, request promotion and wait for the human:

   ```text
   lw agent-promotion request <plan-id> --revision 1 --request-id <stable-promotion-request> --json
   ```

   The separate private controller runs Reference and Candidate held-out work on
   the same queue. It uses `agent-promotion next`, binds a fresh native session
   while work is `pending-native`, requests work again, imports native captures
   through `agent-promotion capture`, and completes that bound session. Its
   `lw-rsi-eval-` credential selects only those handoff tools. It stays outside
   Optimizer and Target. Only the human review card contains both test results
   and the lower-Candidate warning. Human approval performs the verified
   Reference transaction; a train pass alone leaves Reference unchanged.

If private material enters Optimizer input, stop. A full known-private statement
submitted through its granted interface triggers a durable contamination stop.
The controller can report exposure with immutable native record/event hashes
using `agent-round contamination`. A denied read alone is not exposure evidence.
Contamination revokes owned grants, cancels owned Sources, preserves cleanup and
editor blocks, restores Reference files, and permanently stops that round.
The controller can retain a later native usage report privately without resuming:

```text
lw agent-round capture <plan-id> <native-records.json> --json
```
