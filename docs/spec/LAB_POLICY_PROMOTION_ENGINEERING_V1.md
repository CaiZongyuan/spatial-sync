# Policy and Device Program promotion engineering v1

Proposed executable contract for #36 and the promotion-dependent remainder of
#46, based on #35b/PR163 and the snapshot-first #38 contract. This slice does not
claim T0/contact quality or an actual independently sampled training dataset.

## Ownership and entry points

One promotion module owns request, human decision, recovery and deployment
authorization. Policy and scripted Device Program are material adapters; they
share the existing Evaluator queue, single Session slot, ApprovalStore and one
Scene/Controller publication transaction. Scripts have their own exact code
approval requirement and do not inherit the policy observation gate.

```text
request(subject, scene, actor, requestId) -> Promotion
review(promotionId, humanActor) -> private train/test review
decide(approvalId, decision, humanActor, note) -> Promotion
authorize_scene(scene) -> committed deployment permit | refusal
```

HTTP, installed CLI and generic MCP submit the same intent. Existing approval
decisions remain human-only; no approval MCP tool or model proxy is added.
`lw policy promote <id> --scene <name@rev>` selects an exact immutable Candidate.
The request may select an exact train Evaluation/observation check when several
exist; omission never means selecting an unrelated or incomparable success.

#38 owns the shared binding facts in `packages/programs/subjects.py`:
Candidate identity, fixed evaluation Scene, target, complete base bindings,
replacement binding and binding hash. Its three shared operations validate the
base Catalog, validate the applied Catalog and apply the one target binding.
Material, code approval and actual execution validation remain with the policy
or program adapter. Preserve old `lw.policy-subject/1` and `policy_subject`
records while introducing the second adapter; no class/code travels over RPC.

## Admission facts and order

Before queuing any held-out work, reconstruct and freeze:

1. The exact Candidate id/content hash/material hashes and authorized adapter.
2. The fixed Suite, Scene and complete base binding hashes, server-produced
   environment/judge identity, current comparable Reference and its exact
   Candidate identity. Unknown evidence is a refusal.
3. A completed train result for this exact Candidate under these exact
   conditions. Recompute the shipped Wilson lower bound and compare it strictly
   with the current Reference point estimate. An old `accepted` string alone
   cannot authorize deployment after the Reference changes.
4. Policy observation qualification from the trusted verifier below; scripted
   programs instead require their exact currently approved Plan/code snapshot.
5. The simulated Embodiment, Controller current version/hash and immutable
   Robot Model/program composition that will be deployed.

A refusal returns409 with the actual acceptance/qualification reason and starts
no test Evaluation, Session or approval. Statistical acceptance, Hypothesis
judgement and human deployment approval remain separate facts. This does not
change #132 C/D or the Agent acceptance rule.

## Observation qualification and its trust root

The frozen Policy package's `qualified:false` records its creation evidence;
it is not a permanent decision about later independently verified evidence.
Numerical passes and `capture_contract_verified` from #35b are insufficient.
There is no endpoint or CLI option that sets qualification to a supplied bool.

```text
ObservationQualification.verify(policy, check) -> immutable QualificationFacts
  recompute numerical agreement over exact frozen samples
  verify authenticated same-Job/attempt/Task/Scene/export/versions chain
  verify a registered actual native sampling source and its execution witnesses
```

The production adapter recognizes versioned native source profiles, anchored to
gateway-owned collector implementation and reviewed native actor/state-provider
code digests. A profile describes actual sampling, not a caller's origin label.
It must bind the exact exported weights and observation spec, Task source,
succeeded fenced Job, actual trainer/runtime implementation, and at least256
distinct actual state/actor-observation witnesses. Recompute every observation;
maximum absolute error remains <=1e-4. Missing, stale, foreign, duplicate or
tampered facts give an explicit unverified/failed verdict.

The native collector uses the existing owned step/reset observation path. It
copies the actual native actor input returned by the training environment and
the matching canonical physics state/previous action at the same environment,
epoch and step. A Task state adapter may map native physical channels; it cannot
submit a prebuilt actor vector as proof that the native actor read occurred.
Collection does not invoke the platform observation assembler, repeat a step,
read mutable physics from another thread, or edit SDK files. Final export binds
the retained dataset to this Job's exact material.

The profile must name the exact exported ONNX input boundary and normalization
placement. For upstream RSL-RL5.4.1 MLP, native `get_latent` concatenates selected
observation groups before `obs_normalizer`, while `_OnnxMLPModel.forward` accepts
that concatenation and applies a copied normalizer inside the exported graph.
Capture the actual normalizer input during the algorithm's action-collection
call, not a Task-returned actor vector or a later training minibatch. Pin actual
runner/actor/exporter/state-provider implementation digests, group order and
normalizer state/material hashes. An unknown external normalization step or
changed exporter is unverified, even if a supplied vector has the right shape.
Record the actual raw algorithm action used as the next previous-action input;
do not substitute a clipped/scaled Task value merely to force agreement.

Primary input-boundary sources, pinned at upstream commit
`016c7ede710e358b7d6c205642e2540804d6281f` (tag v5.4.1):
[native/exported MLP](https://github.com/leggedrobotics/rsl_rl/blob/016c7ede710e358b7d6c205642e2540804d6281f/rsl_rl/models/mlp_model.py)
and [runner ONNX export](https://github.com/leggedrobotics/rsl_rl/blob/016c7ede710e358b7d6c205642e2540804d6281f/rsl_rl/runners/on_policy_runner.py).
This source inspection is not an executed training experiment.

The old Task hook returning a ready sample list stays a supported unqualified
capture contract. It cannot become a trusted native profile merely by changing
a string or supplying a source hash. Unmatched/unsupported native sources stay
unknown. A real source profile can satisfy the production verifier when its
actual bound evidence exists; the implementation must not return false
unconditionally or test the frozen Policy flag as its final gate.

For transaction tests only, inject an explicitly controlled trusted verifier at
the module's constructor seam. Keep its controlled provenance in the fixture
review/evidence. No HTTP input, environment switch or generic qualified setter
can enable that adapter in production. Passing this seam proves publication and
privacy behavior, not genuine learned quality. Actual experiments/profile
admission still require the separately concrete authorization and evidence.

## Held-out execution and visibility

Only an admitted promotion requests held-out Reference and Candidate scopes
through the existing Evaluator queue. Each scope freezes its purpose, split,
promotion id and exact Candidate before Source allocation. Persist private
intent before Session.start, suppress private Inbox events, and retain actual
partial attempts/recordings/cleanup without creating a zero score from an
invalid run. Revalidate identities and Reference before finalizing the card.

Reuse #45's generic split/privacy rules. Extract its shared implementation when
both consumers exist, including embodied `episodes[].recordingId` recovery; do
not create another restricted-recording registry. Ordinary Evaluation reads,
Session list/get/stream/history, recordings/assets, Notebook, imported runtime
records, Cost detail and Inbox must not expose test questions, scoring inputs,
results, seeds or recordings. A guessed id is not authority. Real private usage
remains in the existing ledger, with restricted public detail/coverage.

Public approval details contain only the promotion identity and human-review
URL; Inbox copies those details, so test scores do not belong there. A dedicated
human-only review supplies train/test summaries and `candidateBelowReference`.
The human card shows both scores and a red warning when Candidate test is below
Reference. Generic CLI/MCP/Optimizer reads remain train-only. Human approval is
required even when both statistical decisions are positive.

## Publication and recovery

The durable Promotion freezes its read set: Candidate/material, train result,
Reference, qualification facts, Suite/environment/judge, base Scene hash,
complete bindings, Embodiment, Robot Model and Controller current hash/version.
Revalidate it on approval and before each new publication effect. Revocation or
changed context before execution makes the old intent stale/blocked, requiring
a new reviewed context. A completed publication is historical, not a hot swap.
Its committed human deployment proof remains independent of later Training Plan
revision/revocation: evaluation loading still requires the current approved
exact Plan/hash, while deployment loading requires this committed exact
Scene/Controller/material proof. Reverting a committed deployment requires a
new explicit transaction; it does not silently roll back an active Session.

Use the existing library validation, import lock and revision reservation. The
transaction reserves one Scene revision and Controller version once, records
expected package bytes/hashes and persists progress before effects:

```text
admitted -> testing -> awaiting_human -> approved
  prepared -> scene_published -> controller_version_written -> committed
```

Controller current is the final commit point. Publish a complete immutable
derived Scene package and immutable Controller version before changing current.
The Scene copies existing physical/asset bytes, changes only the intended
program/config declaration, and passes ordinary Scene validation plus complete
applied-binding validation. Never mutate the old Scene or old recording bytes.
Controller metadata records the exact material, action/observation/Task facts,
new deployment Scene and actual public train Evaluation. Unknown metadata such
as model parameters/licence stays unknown; a taught Model is not relabelled as
a learned policy. Held-out results do not enter public Controller files.

After a crash, recover by verifying the reserved destinations and their exact
hashes: reuse this transaction's already published package/version, finish the
remaining effects, or retain an explicit blocked reason. Never allocate a new
revision for the same retry, bless foreign bytes at a reserved destination,
overwrite another current, or mark success after only updating a pointer.
Concurrent current changes are detected again at the commit point. An orphaned
but attributable partial Scene is not authorized for execution until commit.

Interactive loading of policy/script bindings requires a committed human
deployment proof matching the actual Scene, Controller version, material and
complete applied bindings. An ordinary imported Scene/program reference is not
such proof. Evaluation permits retain their distinct exact-admission/code-Plan
rules. The derived Scene already contains the replacement binding; deployment
loading validates it rather than applying an evaluation override a second time.

## Resource and workflow readback

The robot card and Controller history show the committed new default Scene,
version, composition and exact train result. An active Session retains its own
loaded Scene/Controller identity and is never presented as already switched.
The real Embodiment remains unconnected. Preserve current read-only resource
contracts and distinguish runtime support, actual evaluation and deployment.

Ship `lab-train-evaluate` through the existing wheel/parser/CLI/MCP skill path:
read Plan and prior train results, stay within approved work, evaluate exact
material, inspect public failed recordings, then request promotion or return to
planning. It cannot approve itself or inspect held-out inputs/results.

## First counters and final evidence

Before broad UI work, prove the same public module seam with an explicitly
controlled trusted qualification adapter and actual owned library/state roots:

- Inject failure after Scene publication and before Controller write; restart
  and retry must reuse the same Scene/version and complete, or report blocked.
- Change Controller current concurrently before commit; retain the competing
  value and refuse the stale transaction without duplicate revisions.
- Attempt every ordinary test read, including recording/history/Notebook/
  runtime/Cost/Inbox paths; deny it while the human review remains readable.
- Refuse missing/contract-only observation evidence and stale/unknown/currently
  rejected Candidate conditions before any held-out work starts.

Then cover human approve/deny/revoke, below-Reference warning, old active Session
identity, public resource history, actual installed CLI/official MCP and the
Platform Skill. Final Native/Scene changes require the four Newton CPU checks,
one stable affected-preview capture batch, exact-head full CI, non-author review
and integrated-tree equality. Keep genuine training/contact/256-state quality
unmet until its actual evidence exists.
