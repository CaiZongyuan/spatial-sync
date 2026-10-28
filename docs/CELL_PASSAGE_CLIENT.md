# Cell Passage clients

The Cell Passage scheduler is a client. Device calls and Plan results use the
existing HTTP pipeline; the gateway does not schedule this experiment. The
Python scheduler is `tools/cell_passage_runner.py`. Its fixture tests exercise
the real HTTP action and Plan services with an explicit synthetic actuator;
they do not qualify Newton mechanics or a complete passage.

## Shared documents and identities

`tools/cell_passage_contract.json` is client data shared by the Python recipe
helpers and the browser. It fixes seven stage ranges, starting at 100001,
200001, …, 700001. A step keeps its integer ID through revisions. The JSON is
bundled client data, not a new HTTP endpoint or protocol message. It also lists
public mechanical properties by EntityType for reconciliation. Clients select
only properties actually declared by the current Catalog, discover managed
devices and vessel bodies through workcell config, and read current facts.

An Operation Plan document contains ordinary `do` and `check` steps. Experimental
waits use the workcell's `wait` action with `seconds`, driven by simulation time.
The ordinary wall-clock HTTP `wait` endpoint is not an experimental timer.

Each controlled action carries `owner`, `epoch`, and a distinct domain
`operationId` in its existing params. `requestId` identifies one HTTP submission.
Automatic and browser clients must use mutually exclusive control owners. A
client taking control submits `requestControl`, observes the new epoch and
holding mode, waits for source-reported `controlSafe`, and submits
`confirmControl`. Acceptance of a control request does not prove standstill.
The old owner must stop dispatch and continue observing accepted invocations.
The source enforces generations even when a stale client read preceded takeover.

## Submit once, observe to completion

The offline compiler reads a generated package, its reach teaching, workcell
binding and actual USD Site offsets:

```bash
python -m tools.cell_passage_plan --library <generated-package-root> \
  --out <new-round>/plans --round <round-id> --owner <owner> --epoch <epoch>
```

It writes ordinary stage/part Plan documents and a local recipe sidecar. The
compiler schedules six separate sources, four plus two sample centrifuge
batches with two balance tubes, six counting samples, twelve daughter wells
and 84 fresh tips. Moving between different rail positions uses pick, a held
home retreat, rail motion, and place. A generated document is planned intent:
`mechanicallyQualified:false` prohibits treating compilation or reach teaching
as full passage acceptance. With the environment bindings and managed-device
configuration, it adds controlled incubator `waitReady` followed by public
temperature/CO2/humidity/setpoint, actual door and clean-enclosure checks.
Older packages without those bindings remain `environmentChecksAvailable:false`.
Liquid calls use source-resolved available/fraction/top-up/all-tip intents:
the client does not subtract nominal floating volumes from a local ledger.
PBS, trypsin and counting samples retain literal doses; the fixed literal mix
stroke is 900 uL, leaving inventory margin after actual resuspension.
Disposal uses the authored `tipDiscardSite` when present, and the compiler
rejects a workcell binding that still points to a different disposal Site.

Seven stages are split into ordinary Plan parts of at most 128 steps, preferring
complete transfer, mixing and transport groups. Flattened action order, params
and global identities are unchanged. A boundary never resets contents, releases
a payload or synthesizes success. A round scheduler must execute every part in order under the
same session/scene/control checkpoint and continuous recording; one completed
part cannot be reported as a completed passage. The public ID format is
`cp-{roundId}-stage{stage}-part{part}-of{parts}` and title format is
`Cell Passage {roundId} · {stage}/7 · {stageTitle} · part {part}/{parts}`.
Clients require seven stages, complete consecutive part numbers and consistent
part totals. The browser can read those
ordinary Plans; the local recipe sidecar is not exposed as an HTTP endpoint.

Publish all parts into an existing owned session without starting actions:

```bash
python -m tools.cell_passage_session --session <session-id> \
  --recipe <new-round>/plans/recipe.json --out <new-round>/prepared
```

Adding `--execute` drives every published stage/part in order in the same
process. It checks the session, scene revision, control generation and material
revision at every part boundary and records public stage snapshots plus one
receipt per part. It keeps physical holds and contents across pages. Its
terminal `plans_completed` explicitly keeps `experimentQualified:false`:
separate material, mechanical/environment and continuous-recording acceptance
is still required. A stopped round cannot be reopened by this driver.

Actions use `POST /api/v1/sessions/{sessionId}/actions` with `wait:false`, plus
`planId`, `stepId`, and `planRevision`. Conditions use the existing `/check`
route with the same Plan references. A successful invocation is insufficient:
the current Plan step must be `done`, including its `expect` result.

If an action response is lost, read the Plan's
`stepResults[String(stepId)].invocationId`, then observe that invocation with
GET. Do not send a second action POST. If admission remains unknown, stop
dispatch and retain the interrupted run. A network observation failure does
not classify the device action as failed. The Python scheduler measures active
step budgets in simulation seconds; a pause freezes that clock. Budget expiry
retains the invocation and requires inspection, rather than replaying it.

## Same-process reconciliation and revision

Before resuming, pin sessionId, sceneRevision, session epoch, Plan revision,
controlOwner/controlEpoch/controlMode and materialRevision. Inspect actual
held objects, installed tip/liquid, reservations, vessel positions, lids,
doors, rail and rotor facts. The shared property's `safeEquals` values must
come from the source; clients never calculate `controlSafe` themselves.
Review compound values directly from public state. Scalar conditions may be
checked with `/check`. Check the pinned checkpoint again after the mechanical
checks. Any changed checkpoint requires another inspection.

An in-flight step must finish or be cancelled and actually settle before a
revision. Use `POST /api/v1/plans/{planId}/revisions`; preserve every completed
step's content exactly so its result remains immutable. `revise_remaining` in
`tools/cell_passage_recipe.py` updates the owner/epoch of unchanged, unstarted
pending actions while preserving their domain operation identities and cycle
references. Failed or explicitly replaced actions receive a new operation ID. Failed steps require an
explicit replacement. For partial liquid operations, review appliedUl,
remainingUl, the tip and current source/destination contents and calculate a
replacement for the actual remaining work. Changing operationId and retrying
the original volume is not recovery. The browser may refuse failed-step
continuation until such an explicit revision exists. `revise_round_remaining`
rewrites dependent pending `pellet.cycle` and `completedCycle`/`lastOperationId`
checks across all supplied parts; completed parts and steps are unchanged.
Publish every affected part before dispatch resumes. A new owner/revision uses
a new HTTP requestId for its new attempt; it does not reuse a prior rejected or
unanswered request's cached result.

`PassageExecutor.inspect/reconcile` are local Python methods, not remote APIs.
The browser implements its own client scheduling through the same HTTP
services. It must not call an imagined Python resume endpoint. A new Python
executor accepts only a fresh, approved revision-one Plan with pending steps;
process restart starts a new round rather than treating replay as a checkpoint.

For an existing owned session and a fresh JSON Plan document, run:

```bash
python -m tools.cell_passage_runner --session <session-id> --document plan.json \
  --owner <current-owner> --epoch <current-control-epoch> --receipt <new-round>/run.jsonl
```

The driver appends and flushes observations and an end row. On exceptional exit
it retains the active invocation and requests a new control generation in
holding mode, only if it still owns the original generation. A failed network
request leaves that hold explicitly unconfirmed. It does not claim standstill
or release attachments/material. A receipt lacking an end row is interrupted;
it cannot authorize recovery. `plan_completed` means the Plan finished, with
`experimentQualified:false` until separate material/mechanical checks pass.

## Material result boundary

The owned launch entrypoint wires build, teaching, immutable HTTP import,
validation, Newton CPU launch, control claim and the round client:

```bash
python -m tools.cell_passage --source <read-only-platform_packages> \
  --out .scratch/cell-passage/<fresh-run>
```

It uses API 8023 and viewer 5223 by default, checks both ports before starting,
and gives its gateway an instance header so readiness cannot accidentally select
another server. `--generated <package-root>` reuses an already built/taught
package. `--prepare-only` publishes all Plans for browser execution;
`--no-web --exit-after-run` closes owned services after a CLI trial and finalizes
the session recording. It stops only processes and the session it created.
Fresh output paths and a new round identity are required. The entrypoint keeps
`experimentQualified:false`; running a candidate does not substitute for
independent complete-passage acceptance.

`tools/cell_passage_result.py` reads initial and final public snapshots. It
checks all vessel, tip, counting, waste and residual contents for conservation,
six separate origins, twelve distinct daughter identities, equal live-cell
allocation, the teaching recipe's 20 uL samples and 1000 uL daughter volumes,
and remaining reservations or running/settling operations. Its positive result
is named `material_valid`. Mechanical loading, actual incubation conditions,
processing cycles and continuous recording require separate evidence; this
material result alone cannot declare a complete passage.

`tools/cell_passage_audit.py` separately checks two spatial samples against
their actual Manifest Sites in native coordinates, using the existing labware
seating gates. It also reads finalized recording segment bytes with the
existing codec/store, rejects gaps/truncation/reset episodes, and requires
retained completion of every expected Plan part. These read-only reports keep
`experimentQualified:false`; end-state alignment and readable recording bytes
do not alone qualify loaded transport or a complete experiment.

Before the first complete Newton run, the PM fixed separate gates in the
approved simulation recipe: total per-origin cell conservation `1e-6`, sibling
live-cell difference at most one cell, and final liquid volume tolerance
`0.01 uL`. The sibling tolerance accounts for discrete allocation and float32
stroke resolution; it does not loosen conservation or authorize quantity
snapping. The shared client contract and each recipe sidecar retain these
values so later failures cannot change the gate.

The source quantity-intent actions are `aspirateAvailable` (optional reserved
volume), `aspirateFraction`, `aspirateTopUp` (source plus target compartment and
target volume), and `dispenseAll`. They resolve available amounts against
actual pump position and limits at source startup; literal actions keep their
existing semantics. The shared JSON classifies both families for clients.
After a failed intent, do not run the same fraction/available/all intent again
against a changed state. Review its original requested/resolved goal,
applied/observed amount, current tip/vessels, and explicit untransferred
resolution residual. A liquid replacement must state a reviewed literal
remainder with a new operation identity. Untransferable residue stays at the
receipt's `remainderLocation`; it is not invented transfer or disappearance.
