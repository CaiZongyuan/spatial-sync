---
name: lab-agent-evaluate
description: "Evaluate a fixed Agent Harness through an external runtime, claim public cases, complete or cancel the platform evaluation, and read trusted scores and evidence."
---

# External Agent evaluation

The controller coordinates the work. The runtime creates each fresh Target
conversation and owns its model, credentials, tools and native metering. The
platform owns the Synthetic Session, action policy, final observations and grade.

## Select and freeze

1. Read the exact public Suite and Harness version:

   ```text
   lw suite show suite/lab-ops-basic@1 --json
   lw agent versions lab-operator --json
   lw agent-run create --help
   ```

   Use the Suite's fixed Agent and the user's runtime/model/thinking context.
   Preserve unknown runtime versions. Platform creation performs CPU evaluation
   admission; model execution remains the runtime's separately authorized work.

2. Start a bounded verification case before a full train scope:

   ```text
   lw agent-run create lab-operator --suite suite/lab-ops-basic@1 --harness-version 1 --runtime claude-code --model <model-id> --thinking <thinking-mode> --purpose verification --case pause-reset --wall-seconds 120 --request-id <request-id> --json
   ```

   Retain the returned run ID and fixed input hashes. Verification runs one
   repetition and publishes no complete evaluation. For a full evaluation,
   choose `--purpose train`, omit `--case`, and retain every required repeat.
   Retry the same request ID with unchanged inputs to recover the same intent.

## Handoff and completion

3. Claim work as the controller:

   ```text
   lw agent-run next <run-id> --json
   ```

   `pending` means the shared Session slot is occupied; preserve its owner.
   `work` returns the same active case on retries. Give the runtime only the
   public statement, exact public Harness/skill contents and case connection.
   Configure a fresh Target with that case's `LW_TOKEN` and `LW_SESSION` and
   the scoped platform MCP. Keep controller credentials outside the Target.
   The supported Target configuration uses those scoped tools; additional
   Shell/raw HTTP capabilities are the runtime's configuration responsibility.

4. Let Target use the public Catalog and platform tools to attempt the task.
   Platform policy rejects human-gated actions and operation Plans during
   evaluation. Report the observed refusal and correct ordinary precondition
   errors through the same tools. Target's tools exclude controller operations.

5. When that fresh Target ends, retain its actual native session ID. If it
   produced a supported capture, use [lab-runtime-records](../lab-runtime-records/SKILL.md)
   to import it and retain its returned record ID. Complete from the controller:

   ```text
   lw agent-run complete <run-id> --case-run <case-run-id> --native-session <native-session-id> --runtime-record <runtime-record-id> --json
   ```

   Omit `--runtime-record` when metering is unavailable; keep it unknown. Each
   independent repetition requires a fresh native session identity. Completion
   signals the platform to take its own final observation and grade; scores,
   final state and elapsed time come from the platform. A completed failed task
   can be a valid scored negative. Retrying completion returns its saved outcome.

6. Read the retained result, then claim the next case until `finished`:

   ```text
   lw agent-run show <run-id> --json
   lw score ls --suite suite/lab-ops-basic@1 --json
   ```

   A complete train result requires all public cases at their fixed repeats.
   Its 0–100 score is `reported`; it changes no Reference or promotion decision.
   Inspect case Trace/final/recording evidence and native cost provenance.
   Runtime declarations are labelled declarations, not isolation attestations.

## Cancel and recover

```text
lw agent-run cancel <run-id> --json
```

Cancellation revokes platform grants and closes its owned Source; the runtime
stops its own conversation. `cleanup.blocked` means the shared slot stays held.
Report that fact and use the existing operator cleanup interface at
`POST /api/v1/evaluations/{id}/cleanup`; confirm `cleanup.complete` before new
work. Restarted or expired work cannot reuse its old grant or publish a complete
scoreboard. Preserve invalid attempts and native record links.

For an external Target that must be separated from the global operator, use the
gateway's existing authenticated remote/operator-token mode. Anonymous local
development retains its ordinary operator behavior. The platform grants its
interfaces; it does not attest or sandbox a runtime's other files or networks.
