# Epic Simplification Survey

Read for epic completion. Adapted from DeepSeek Harness's `dsh-find-simplifications` guidance at revision `477b4f420553e8a52c2fbccc464d7561b239c443`. The procedure below is self-contained and follows the host repository's rules.

## Establish the Whole Epic

Read the parent specification, relevant child acceptance criteria, linked implementation PRs, applicable `AGENTS.md`, and accepted design and domain decisions. Verify integration from commits and current code; closed issues alone do not prove delivery.

Record the pre-epic base and integrated head, then inspect the full range and current consumers. If no reliable pre-epic base is recoverable, use an explicit child-PR or commit inventory and identify affected domains from it. State scope gaps. An empty diff against a merged branch or a single final child PR cannot establish whole-epic coverage.

Consider the combined user journeys, generated contracts, runnable applications, tutorials, and examples where present. Check whether examples can be removed independently when the project requires it. Distinguish missing required behavior from opportunities to reduce future maintenance.

## Search for Removable Maintenance

Follow the epic's actual modules and dependencies, including production machinery, tests, configuration, build and deployment scripts, and documentation. Use these questions where relevant:

- Does a field or feature have a complete path from producer through transformation and provider or endpoint to an observable consumer? A declaration or test alone does not prove a production effect.
- Do multiple public states, APIs, or configuration options change a consumer's action? Preserve distinctions that govern permissions, durability, ownership, or recovery.
- Can a consumer read an authoritative value instead of maintaining a copied history, cache, or projection? Establish when it needs that value and what consistency is required.
- Do repeated wrappers, parallel application trees, or pass-through configuration own behavior, or only duplicate maintenance? Include remaining glue in the cost comparison.
- Would a smaller explicit behavior remove a subsystem? Name the lost capability; this is a product decision rather than an automatic cleanup.
- Can an existing dependency replace owned infrastructure with less total maintenance? Include dependency cost, failure behavior, and residual adapters, not just removed lines.

Start with `rg`, then read the matches. Search symbol uses, writes as well as reads, wire and configuration strings, registrations, manifests and exports, generated consumers, scripts, and relevant external extension contracts. Zero repository callers alone does not justify deleting a public capability. Trace dynamic assembly and code generation before declaring code dead.

## Evaluate Each Candidate

Record the owner and concrete file or symbol evidence, the effective producer and consumer path, the code, state, configuration, tests, and documentation that disappear, what remains, and the strongest reason to keep it. Classify it as:

- **Behavior-preserving candidate:** evidence supports removing unused or duplicated obligations.
- **Behavior or architecture decision:** a public capability, accepted design decision, or product behavior would change; identify the exact trade-off.
- **Retain or defer:** the distinction serves a required behavior, merely moves complexity, or has insufficient evidence.

Preserve the host repository's required architecture boundaries, ownership rules, and supported deployment or extension behavior as established by `AGENTS.md`, specifications, and accepted decisions. Preserve required security checks, transaction and recovery semantics, migration and data compatibility, and independently observed regression tests. Collapsing code while losing those obligations is not simplification.

## Deliver a Bounded Report

Report a small candidate table with evidence, removed maintenance, retained obligation or capability loss, validation needed, and recommendation. Include meaningful rejected or deferred candidates and unreviewed areas; no candidate quota or deletion target is required.

Reuse existing decisions and issues where applicable; document durable architecture changes according to the host repository's conventions. Execute already approved candidates as bounded changes with affected validation and independent review. Required behavior defects affect epic acceptance; optional simplifications can be deferred without withholding completion. A finding supported by evidence that no worthwhile simplification remains is valid.
