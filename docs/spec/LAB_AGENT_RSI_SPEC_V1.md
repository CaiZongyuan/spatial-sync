# Agent RSI v1 — external runtime workflow

This implements #45 within RSI §7–9 and Web UI §5.6. External runtimes execute;
the platform freezes approved intent, restores Harness files and makes decisions
from its own complete evaluation evidence. #18 and Scene Factory are excluded.
No model launcher/proxy, provider pricing, credentials relay, second evaluator
queue, usage ledger or #132D statistical policy is added.

## Records and fixed conditions

An Agent training Plan has `kind: training`, `layer: agent`; legacy Plans without
a layer remain embodied. It identifies an explicit Goal, Agent, immutable Suite,
Reference and candidate Harness, fixed runtime/model/thinking declaration, one
bounded change, hypothesis, finite platform limits and native-cost allowance.
It contains no fabricated Task, PPO algorithm, GPU Job or provider price.
Existing revision-specific human Plan approval remains mandatory. The approval
subject remains `plan`; agents cannot approve, deny, revoke or promote.

A formal baseline references a complete valid public train evaluation for the
exact Reference Harness/skills and Suite/Scene. Its cases meet the fixed repeat
count (at least three). Verification, partial/invalid runs, imported claims and
missing comparison identities cannot create a baseline. Environment and judge
identities are server-produced read-only facts; unavailable facts block a
comparison rather than becoming empty/default identities.

Each round retains Plan/revision, Reference and candidate versions/hashes,
platform evaluation IDs, conditions, native record references and observations,
hypothesis judgement, acceptance decision and actual file-transition receipts.
Agent mean scores use0–100, `ci95:null`, actual min/max case scores and repeat
counts. Only a strictly higher complete train mean passes the existing Agent
acceptance rule. B's append-only `reported` result remains separate from R3's
decision, Reference and human promotion.

## Harness transitions

Before editing, verify the Reference archive. A transition durably records its
request and expected current version/content hash before swapping files. Stage
only a verified confined archive, replace the editable Harness under the Agent
lock, then verify actual file bytes and commit current/Reference metadata and
the outcome receipt. Exact retries return the same receipt. Restart recovers an
interrupted swap; an unexpected user edit produces a preserved conflict.
Never mark restoration complete merely because a pointer or DB flag changed.

Rejected/contaminated snapshots stay exportable and their version numbers are
never reused. Restoration selects the prior Reference files/current pointer.
Allowed candidate edits are one AGENTS.md behavior block, one Agent-owned skill
or one explicitly permitted Harness configuration field. Product skill pins
and Agent-owned skills resolve exact frozen bytes. No global skill is rewritten.

## Evaluation, promotion and contamination

Approved rounds call the existing B evaluator/Session queue. Target retains the
case-scoped connection and platform action/approval policy. Ordinary B runs
remain usable without a training Plan. No caller score or final state is an
authoritative evaluation input.

Promotion requests alone may drive held-out cases through the same queue, with
fresh isolated Target conversations configured by the external runtime. Obtain
comparable Reference test evidence when absent. Only the human promotion card
shows those results, warning when Candidate is below Reference. Test statements,
results and private evidence never reach Optimizer's actual tools/HTTP, ordinary
run/score/Notebook/cost/approval/inbox projections. No public `split:test` bypass
is introduced. Reference advancement requires this human decision and a verified
Harness transaction; a manual pointer flip does not implement promotion.

An exposure report references a fixed round and immutable runtime/event evidence.
Trusted exposure signals or controlled private markers exercise contamination;
denied reads alone do not prove contamination. On contamination, revoke owned
B grants, cancel only the platform's Source, retain incomplete cleanup blocks,
restore the Reference files and stop the Plan/round as `contaminated`. It cannot
later accept or resume. Public records contain no leaked private material.

Use authenticated remote/operator-token mode and a restricted external-runtime
MCP configuration. Keep controller credentials outside Optimizer/Target. The
platform enforces its granted interfaces; it does not attest arbitrary external
Shell/files/networks or implement an OS sandbox.

## Notebook, cost, clients and verification

Agent Notebook variants preserve the embodied formats and human override audit.
Use A's explicit Agent/Harness/Plan/Goal associations and typed native high-water
facts. Missing stays unknown, explicit zero stays zero, and revisions share the
original Plan/Goal pool and clock. Observed spending can stop future admissions;
it is not a pre-call hard USD guarantee over an external runtime. Historical
completion snapshots and live A costs display their distinct basis/hash/time.

HTTP, installed CLI, generic MCP, `lab-eval-build`, `lab-harness-optimize` and
the Agent training page share these records. The page has layer/Agent/Goal URLs,
the specified stages, Reference/candidate,0–100 mean/range plot, hollow rejected
versions, Harness history/composition, external runs, budget and Notebook.

Prove real file restoration, failure/restart recovery and concurrent edit
conflicts first. Controlled external controllers use actual Synthetic grading
for accepted/rejected rounds and restricted held-out handoff. Cover approval,
scope, unknown identity, contamination and test-data bypasses through the granted
interfaces; verify Notebook/native cost agreement and installed skills/clients.
Run mandatory checks, built-browser journeys, bounded simplification, independent
review, exact-head CI and integrated-tree verification. Actual paid runtime work
still requires later concrete model/budget authorization.
