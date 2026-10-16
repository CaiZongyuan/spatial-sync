# Local Simplification Before Review

Read for issue or PR completion. Adapted from Anthropic's [code-simplifier](https://github.com/anthropics/claude-plugins-official/blob/ceb9b72b4c4c20ad39efce780edd0aabe80ebce3/plugins/code-simplifier/agents/code-simplifier.md): preserve behavior, focus on recently modified code, and prefer clarity over brevity. The procedure below is self-contained. Follow the host repository's language and style conventions; the upstream agent's JavaScript style rules and model selection are not requirements.

## Bound the Edit

Start from the task's acceptance criteria and working behavior. Inspect its changed code plus the immediate callers needed to understand it. Apply improvements within the same issue; discoveries elsewhere become follow-up proposals.

- Flatten unnecessary nesting and make names, conditions, and error paths explicit.
- Remove redundant intermediate state, duplicate logic, or pass-through abstractions when their consumers demonstrate no distinct responsibility.
- Keep useful domain boundaries and ownership visible. Similar-looking code with different reasons to change need not share an abstraction.
- Remove comments that merely narrate code while retaining non-obvious behavior, failure, ordering, and ownership facts. Update the owning source before regenerating derived artifacts.

The result should be easier to understand or maintain. A line-count reduction, generic helper, or new dependency alone does not establish an improvement. Preserve existing features, outputs, public types, errors, authorization, persistence, transaction behavior, and relevant timing and resource guarantees. A capability reduction is a proposal, even if it deletes substantial machinery.

## Protect Required Behavior

Read the host repository's applicable `AGENTS.md`, specifications, and accepted decisions before removing an architectural distinction. Establish why a boundary or ownership rule exists and which consumers depend on it. One current consumer is not evidence that a supported distinction is redundant.

Keep validation at untrusted inputs and persistence or wire boundaries. Preserve required idempotency, cancellation, cleanup, rollback, recovery, and permission enforcement where touched. Derive the actual obligations from project rules and behavior. Regenerate generated contracts and client code from their owning source rather than simplifying generated output manually.

Keep behavior tests independent of implementation structure. Refactoring may simplify fixtures, but must retain distinct regression evidence. Add a test only for a meaningful uncovered risk, not to mirror a renamed helper.

## Finish the Pass

Explain significant changes and deferred trade-offs, or state that no worthwhile local simplification was found. Run affected validation and obtain independent review against the host repository's standards and the task specification. Large refactors and optional cleanup do not become mandatory work merely because this pass found them.
