# Advisor Consultation

Use a read-only second opinion for a consequential unresolved design, repeated failure without new evidence, conflicting review findings, or an explicit request. Ordinary tickets continue through implementation and independent review. Consult after forming a specific question and concrete candidates.

## Evidence Packet

Give the configured Advisor or an available independent Agent only what resolves the question:

- Original relevant requirement, latest correction, and governing contract.
- Decision or failure, current hypotheses, candidate options, and the exact question.
- Relevant task-owned diff or source locations, counterexample, original error, and actual verification. Identify the baseline and complete candidate, including new/uncommitted files when relevant.
- Attempts already made, what changed, what remains unknown, and links for additional evidence.

Keep relevant evidence verbatim; mark omissions and redact secrets. A long history is a reference, not the default packet. For example: “Both saves return conflict with the same submitted version. Is a retry justified, or does the public version contract require refreshing first? Here are the request, error, and two proposed fixes.”

## Task and Response

The Advisor may inspect relevant code and evidence. It recommends a decision or discriminating probe; it does not edit, execute heavy validation, change the tracker, publish, or merge. Use the actual runtime's supported Agent and model capabilities. Independent context is useful by itself; describe vendor/model diversity only when actually available.

Request a short response with:

1. Recommendation: proceed, proceed with a stated change, or pause for a stated missing fact.
2. Evidence and reasoning, with locations and falsifiable assumptions.
3. One best next action and its observable success/failure condition.
4. Remaining risk or uncertainty.

The PM tests the recommendation against requirements and facts, records the decision and reason, and dispatches the next action. Consensus or an Advisor verdict cannot substitute for independent Standards + Spec review, required final-head CI, or actual integration.

## Reuse and Limits

PM owns consultation state in the existing run/ticket checkpoint. Reuse the same Advisor for a follow-up and send only the delta and new question; keep unrelated runs/tickets separate. No shared Cursor state, hooks, fixed model slug, or always-on reminder is needed.

At the phase's budget or exit condition, decide whether a new probe can distinguish remaining causes. Further consultations need a new question or evidence. If the channel is unavailable, record the limitation and continue only the decisions supported by current evidence. An unresolved required fact remains unresolved.

Method adapted from [Cursor Advisor at df581122](https://github.com/cursor/plugins/blob/df581122cde17e6e27686b5a448bde23e4ad4318/advisor/skills/advisor/SKILL.md); runtime calls and lifecycle follow the host.
