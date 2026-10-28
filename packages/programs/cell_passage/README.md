# Cell Passage workcell interface

`cell_passage.workcell` implements `cell-passage-workcell/v1`. It is the sole
writer of Flex XYZ, the pump joint, experimental contents and reservations.
Other new scene programs call `guard(ctx, controlEntity, params, resources)`
in accept/start/tick and establish actual safe hold on a failed guard.
The gateway imports the engine-free registry, never the factory or solver.

The scene binds this program to `/World/Lab/LiquidHandler`. Content IDs are
configuration values, distinct from body entity keys and culture identities.
Clients discover them from the Catalog config and `materialSnapshot.vessels`.
Each plate has six rows with a shared entityKey and separate moving well sites.

All actions require `owner`, `epoch` and a new domain `operationId`. Initially
these are `auto`, `0`, and a caller-selected unique string. API `requestId`
retains its existing meaning. A material operationId is never re-executed,
including when its invocation was partially cancelled. Read its recorded
`appliedUl` and current tip/vessel contents, then create a new operation for
the current remainder. A control operationId can retrieve its original receipt;
reuse with different parameters is rejected.

Cancellation uses the existing Program cancellation lifecycle: an executing
invocation remains running with operation status `settling` and phase `holding`
while measured braking displacement is accounted. Only actual standstill at
the fixed hold target completes cancellation. Reservations, tip attachment and
liquid remain held throughout. Controlled braking return flow is accounted as
net reverse liquid movement; uncommanded reversal during normal execution
still invalidates the material estimate. Queued cancellation is unchanged.
For an executing cancel callback, terminal accounting is finalized on the next
program tick, even if it was already physically still. This lets an existing
Core timeout failure retain priority over a premature cancelled receipt; it
does not release reservations or permit a new liquid action in that interval.

| Action | Additional parameters | Evidence and effect |
|---|---|---|
| requestControl | nextOwner, mode (`auto` or `manual`) | Concurrent. accept increments epoch and commits holding while paused; writes safe targets, retains attachments/liquid. |
| confirmControl | none | Concurrent. Requires actual workcell and managed-device standstill. accept transfers owner/mode and reservation generation; start only acknowledges the receipt. |
| reserve | resource, device | Reserves one body or station for that device. Conflicting device is rejected. |
| release | resource | Requires no liquid in flight, actual workcell axis/pump standstill, and safe managed devices. A still-held body cannot be released. |
| move | position: [x,y,z] | Raw joint positions in metres. Actual feedback and standstill determine completion; clients must use the scene's collision-checked route. |
| pickupTip | tip | Requires actual body/dynamic dock/head/socket fit, seated stock-tip relative velocity, measured head/pump standstill and zero piston before the predeclared physical weld. |
| discardTip | waste | Requires configured solid-waste site alignment and waste capacity. Residual liquid/cells enter that waste row; the actual tip weld is released. |
| aspirate | vessel, volumeUl | Actual positive piston displacement transfers homogeneous suspension into the tip. Attached and pellet cells remain. |
| dispense | vessel, volumeUl | Actual negative piston displacement transfers tip contents into the destination. |
| mix | vessel, volumeUl | Empty tip, real outbound and return piston strokes; each partial stroke changes contents. Completed return resuspends the pellet. |
| aspirateAvailable | vessel, reserveUl (optional, defaults to 0) | Resolve current source volume minus the reserve. Resolution residue stays at the source. |
| aspirateFraction | vessel, fraction (0 < fraction <= 1) | Resolve this fraction of current source volume once at startup. A later retry cannot reinterpret the original fraction. |
| aspirateTopUp | vessel, targetVessel, targetVolumeUl | Aspirate the target's actual deficit minus existing clean tip liquid. Both bodies must be reserved; target lid/station/culture and capacity are rechecked. |
| dispenseAll | vessel | Resolve current tip contents. Quantization residue stays in the tip and is recorded when discarded. |
| wait | seconds | Teaching wait driven by simTime; pause freezes it. |
| detach | vessel | Requires actual trypsin addition and configured teaching exposure; moves attached live/dead cells to suspension without creating cells. |
| pellet | centrifuge, cycle, vessels: [content ID] | Requires completedCycle==cycle, phase idle, completedLoad containing all bodies, cycleRunSeconds>=configured teaching time and actualRotorTurns>0. The centrifuge must retain reservations for itself and each processed body at the current owner/epoch through material application; accept and start recheck them. Each vessel accepts that cycle once. |
| observe / count | vessel | Requires actual vessel facts station==imagerStation and settled. Publishes a deterministic simulated observation, preserving model truth. Sampling is an ordinary actual aspirate/dispense to a separately configured counting well. |

Liquid actions require a reservation of the vessel entityKey assigned to the
workcell. The vessel's configured lidProperty must be false, requiredStation
must match actual settled facts, and the physical tip must align with its site.
The same checks run at start and during execution. A sample-contact tip cannot
enter another culture or a common reagent. A dispense into a culture also marks
contact, even when the tip becomes empty. Use fresh tips when returning to reagent.
Waste cannot be aspirated or mixed back into the experiment. A tip that enters
waste retains `contactWaste=true` even when empty and may only discharge to
waste or be discarded. Isolation checks use actual cell origins as well as the
declared culture/parent and contact history; a blank identity does not erase
cell contamination.

Pickup uses the factory's same-source tipGeometry, not only unused state or a
static rack label. The tip body Root is its fluid endpoint. Body Root and the
rotated mating seat must still match the current dock; the actual head endpoint
must match the actual tip endpoint and seat. Both actual nozzle-segment ends
are projected against the actual socket axis and must fit its positive radial
clearance and axial insertion range. Separate head-to-rack and tip-to-rack
errors cannot be added into an unsafe assembly gap. Axis inversion/tilt,
unknown/nonfinite pose or velocity, an absent descriptor, a dropped/unsettled
tip and a socket without clearance reject before any weld or inventory write.
Circular yaw is intentionally unconstrained. Tip/dock relative speed must be
below the existing seated-station 0.01 m/s threshold; head motion uses the
existing measured joint velocity rules. Intake and startup both read the
current physical facts.

The calibrated descriptor uses mating offset [0,0,0.05] m, socket insertion
depth 0.012 m, minimum straight socket ID 0.0108 m and maximum nozzle OD
0.0100 m (0.0004 m radial gap). The new layout supplies alignmentToleranceM
0.0003. The guard limits positional checks by that calibration and the actual
geometric gap, and rotates both mating offsets/axes by their actual poses.
It does not infer an arbitrary yaw-angle gate or replace actual body pose with
the expected dock pose. These guards do not by themselves qualify the complete
physical pickup route or installed-tip clearance.

The original aspirate/dispense/mix volumeUl remains a literal requested amount;
its stock checks and calibration are unchanged. The four quantity-intent actions
resolve from the single current snapshot, actual pump position and copied
physical joint limits. Unknown/nonfinite limits fail closed. They reserve one
native float32 coordinate quantum toward the starting position and round the
command in the safe direction. Liquid and cells always follow measured pump
displacement, never the rounded command or nominal requested amount. Large
stock, tip-capacity or travel shortfalls reject; only explicitly retained
resolution/travel residue within the fixed 0.01 uL budget can remain unresolved.
There is no hidden air, overflow or artificial liquid correction in this rule.

Receipts add quantityKind, resolvedUl, remainingUl, resolutionResidualUl,
resolutionUl, untransferredUl, pumpStartM/pumpTargetM, quantityOutcome,
remainderLocation and noTransfer. For intents, requestedUl is the ideal amount
resolved once at startup; resolvedUl is the feasible command amount.
appliedUl/observedUl remain actual progress. resolutionResidualUl is the
ideal-to-command difference, remainingUl the command-to-observation remainder,
and untransferredUl the ideal-to-observation remainder. The liquid remains in
the named source vessel or installed tip. For a literal mix, progress and
remaining amounts span both actual stroke legs. Global cell conservation stays
at 1e-6; sibling live-cell allocation tolerance is 1, and final-volume tolerance
is 0.01 uL. Allocation tolerance never relaxes the global material audit.

Already-filled top-up, empty source/tip and below-resolution cases succeed with
noTransfer=true, resolvedUl=appliedUl=0 and an explicit quantityOutcome such as
at_target, empty_source or below_resolution. No pump target is issued. A
positive resolved target instead requires positive observed progress. Once
actual position repeats across three distinct physical frames and the total
untransferred amount meets the fixed 0.01 uL budget, the program establishes
holding targets at actual positions with zero target velocity. It retains the
running handle and continues accounting until the original measured-standstill
checks pass; a nonzero velocity is never reinterpreted as zero. The final
remainder is checked again after braking. A repeated getter, paused clock or
zero first control sample cannot trigger completion. Cancellation, ownership
loss, cabinet/target failure or an already-failed invocation override normal
completion. Even a zero-volume visit to a culture keeps
conservative tip-contact isolation. Failed/cancelled fraction/all/top-up intents
retain their original resolved target and partial progress. Recovery requires
a new reviewed domain ID with an explicit remaining literal quantity or a new
intent based on current vessel/tip truth; do not copy and re-evaluate an old
fraction against an already-decreased source.

The configured `cabinetEntity` must publish `cabinetReady=true` before pickup,
discard, aspirate, dispense, mix or detachment. Accept and start both recheck;
an in-flight readiness loss (false or unknown) writes fixed holding targets,
accounts actual braking flow and fails only after measured standstill. The
tip, liquid and reservations remain. Wait, cancellation, control handoff and
the cabinet's own guarded repair/setter actions remain available. The complete
scene includes `/World/Lab/Incubator` and `/World/Lab/LiquidHandler/Base` in
managedDevices; their readiness/environment signals are explicitly modeled.

`materialSnapshot` is one fixed-field object containing revision, reconciled,
vessels, tips, installed tip, reservations, operations, observations and
controlRequests. Every source/tip/destination change publishes one snapshot.
Vessel rows expose volumeUl, components, cells, culture/parent, mixed,
exposureSince and processedCycle. Cell rows retain their originating culture,
parent and six live/dead attached/suspended/pellet quantities. The vessel's
configured culture/parent identifies daughters independently of origin cells.
Explicit counting, waste and residual vessel contents remain in the same audit.

`operations` retain action, vessel, requestedUl, appliedUl, observedUl, status,
epoch and startedSimTime. For mix, appliedUl counts both legs. Reverse or
overtravel outside configured pumpToleranceUl stops the mechanism, records a
fault and marks `reconciled=false`, `auditPassed=false`. It cannot be resumed as
a verified transfer. Values are estimates through the last valid pump sample;
observedUl records the discrepant mechanical feedback. Preserve that interrupted
run and inspect it; this is not a persisted execution checkpoint.

`operations` is a live recent-history projection, not the complete journal.
It retains the newest 64 ordinary terminal rows plus every running/settling
row, every fault, and every non-successful partial with appliedUl>0. These
protected rows are not silently removed after later successful calls. Row
fields also identify invocationId, owner, original paramsJson, tipId,
endedSimTime and terminal materialRevision. `operationCount` counts material
operations that actually passed startup validation; `terminalOperationCount`
counts their completed accounting receipts; `archivedOperationCount` counts
rows removed from the live projection. Thus retained row count plus archived
count equals operationCount. Counters are available in the snapshot and as
top-level Plan-compatible scalars; do not infer total completion from rows.length.
Control requests and source-rejected/unstarted/queued-cancelled calls are not
included in these material counters and keep their ordinary invocation evidence.

Every material operation that completes source-side accounting emits one declared `operationCompleted`
EntityEvent containing its complete terminal receipt, including failed and
cancelled outcomes after braking has been accounted. Success.result also
contains the same receipt. The existing reliable event stream and recording
store retain these records independently of the live 64-row projection;
snapshot observations precede the event and terminal invocation update. History
consumers must honor the existing HTTP event gap indicator and use the recording
for history older than the gateway's bounded event buffer. The generic recording
reader replays the original receipt events and compact snapshots unchanged.
There is no new database or recording/message format. Source-side used-ID
tombstones are independent of projected rows and remain for that Program
instance's lifetime, including source resets. A reset starts fresh round
counters/material state; new round actions need new domain IDs. A new Source
instance starts a fresh lifetime. Control receipt replay remains unchanged.
The workcell's control generation increases on a same-instance Reset as well
as on handoff. A genuinely new Source starts at generation zero; clients must
always read the current snapshot and must not assume Reset means zero. The
outer Source epoch still rejects already-wrapped old requests, while the
monotonic workcell generation also rejects delayed old action parameters wrapped
by the gateway after Reset. Old control IDs retrieve their original receipt
without changing this round's owner/generation/mode or adding an effective
control request to its snapshot. Compare the receipt with current state;
retrieval does not establish that the old request was applied to the new round.
Round/process interruption retains the ordinary invocation and last observed
state evidence; an exited Source cannot invent a final-accounting receipt.

Top-level Plan-compatible scalars are controlOwner/controlEpoch/controlMode,
controlSafe, phase, materialRevision, auditPassed, samplesIndependent,
inFlightUl, tipsRemaining, wasteFreeUl, lastOperationId, lastAppliedUl,
remainingUl, observationRevision, lastObservedLive/lastObservedDead and the
three operation counters above.
Quantity checks can also use lastResolvedUl, lastResolutionResidualUl,
lastUntransferredUl and lastQuantityOutcome without JSONPath.
No PlanStatus, InvokeRequest, JSONPath or recording shape changes are needed.
Observe/count records identify the source as `simulated-state-model`, retain the
material revision, vessel/culture/parent, and separate measured/model counts.

Required config is axisJoints[3], pumpJoint, tipSite, pipetteBody, cabinetEntity,
vessels[{id,entityKey,site,capacityUl,...}], tips[{id,site,bodyKey}], contentSites
and tipRackSites. The last two are unique top-level Site references that must
exactly cover the corresponding nested sites; this lets the existing loader
validate and materialize every actual well/rack Site. Configure
managedDevices, imagerStation, wasteSite and vessel lidProperty/requiredStation
for the complete scene. Pump mapping defaults to 100000 uL/metre (1000 uL over
10 mm), tipCapacityUl=1000, alignmentToleranceM=0.003, axisToleranceM=0.0005,
holdSpeedTolerance=0.0001, pumpToleranceUl=0.01. These are fixed engineering
configuration, not a permission to loosen a failed physical qualification.
The exporter reads layout.pipetteCalibration.alignmentToleranceM when supplied
and layout.tipDiscardSite for the drop point. wasteSite is an optional top-level
Site reference, omitted when None, so a separate above-rim Discard point is
materialized rather than replaced by the liquid Content point.
Pickup additionally requires tipGeometry with tipMatingOffsetLocalM,
tipAxisLocal, headMatingOffsetLocalM, headAxisLocal, socketDepthM,
socketInnerDiameterM and nozzleOuterDiameterM. The exporter selects these
fields from layout.tipGeometry, leaving asset/hash/actual bounds in the
immutable mechanical-layout receipt. An older layout without the descriptor
remains inspectable but cannot silently qualify tip pickup.

The teaching recipe uses 200 uL PBS wash, 200 uL trypsin and 2 simulated seconds,
then 800 uL medium for each independent 1000 uL collection. After pelleting,
retain 50 uL residual, top up to 1000 uL and mix with a fresh tip using a literal
900 uL actual outbound/return cycle. Remove 20 uL into the matching
counting well, split the remaining 980 uL as 490/490 uL to its two daughters,
and add 510 uL medium to each. Four samples form the first mass-balanced batch;
the remaining two use the configured two balance tubes in the second batch.
This is a teaching simulation, not a real biological protocol. Actual cycle
load/rotation and every liquid action still need mechanical evidence.
These recipe quantities are targets. Use available/reserve, fraction, top-up
and all-tip intents for current-state amounts. A succeeded stroke retains its measured
applied quantity within the fixed tolerance; it never rounds contents to the
requested amount. Subsequent literal transfer parameters must come from current
tip/vessel contents and receipts, with pending Plan steps revised when needed.
In particular, a nominal full aspiration does not establish that a subsequent
exact nominal dispense has sufficient liquid. The complete executor must handle
that numerical boundary rather than changing stock guards or inventing liquid.

Focused tests: `python -m pytest tests/unit/test_cell_passage.py tests/unit/test_cell_passage_journal.py
tests/unit/test_cell_passage_quantities.py
tests/unit/test_cell_passage_tip_pickup.py
tests/gateway/test_cell_passage_api.py -q`. The deterministic external actuator
fixture proves Host/API/recorded-state semantics. It does not prove Newton
geometry, real tip pickup or a full Cell Passage run.

To generate the actual workcell binding from the factory's mechanical receipt:
`python -m packages.programs.cell_passage.scene --layout <mechanical-layout.json>
--out <workcell-binding.yaml>`. Merge that document's entities into the generated
scene devices.yaml before immutable import. The command validates the Program
config, emits six mothers, twelve daughters, six counting compartments, six
sample tubes, two balance tubes, three reagent reservoirs and two waste rows,
and retains the factory's actual 96 tip bodies/sites. It performs no physics
qualification and does not overwrite the mechanical bindings.
