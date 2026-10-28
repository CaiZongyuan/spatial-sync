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

| Action | Additional parameters | Evidence and effect |
|---|---|---|
| requestControl | nextOwner, mode (`auto` or `manual`) | Concurrent. accept increments epoch and commits holding while paused; writes safe targets, retains attachments/liquid. |
| confirmControl | none | Concurrent. Requires actual workcell and managed-device standstill. accept transfers owner/mode and reservation generation; start only acknowledges the receipt. |
| reserve | resource, device | Reserves one body or station for that device. Conflicting device is rejected. |
| release | resource | Requires no liquid in flight, no active workcell motion, and safe managed devices. |
| move | position: [x,y,z] | Raw joint positions in metres. Actual feedback and standstill determine completion; clients must use the scene's collision-checked route. |
| pickupTip | tip | Requires actual tip-site alignment and zero piston, then activates the predeclared physical tip weld. |
| discardTip | waste | Requires configured solid-waste site alignment and waste capacity. Residual liquid/cells enter that waste row; the actual tip weld is released. |
| aspirate | vessel, volumeUl | Actual positive piston displacement transfers homogeneous suspension into the tip. Attached and pellet cells remain. |
| dispense | vessel, volumeUl | Actual negative piston displacement transfers tip contents into the destination. |
| mix | vessel, volumeUl | Empty tip, real outbound and return piston strokes; each partial stroke changes contents. Completed return resuspends the pellet. |
| wait | seconds | Teaching wait driven by simTime; pause freezes it. |
| detach | vessel | Requires actual trypsin addition and configured teaching exposure; moves attached live/dead cells to suspension without creating cells. |
| pellet | centrifuge, cycle, vessels: [content ID] | Requires completedCycle==cycle, phase idle, completedLoad containing all bodies, cycleRunSeconds>=configured teaching time and actualRotorTurns>0. Each vessel accepts that cycle once. |
| observe / count | vessel | Requires actual vessel facts station==imagerStation and settled. Publishes a deterministic simulated observation, preserving model truth. Sampling is an ordinary actual aspirate/dispense to a separately configured counting well. |

Liquid actions require a reservation of the vessel entityKey assigned to the
workcell. The vessel's configured lidProperty must be false, requiredStation
must match actual settled facts, and the physical tip must align with its site.
The same checks run at start and during execution. A sample-contact tip cannot
enter another culture or a common reagent. A dispense into a culture also marks
contact, even when the tip becomes empty. Use fresh tips when returning to reagent.

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

Top-level Plan-compatible scalars are controlOwner/controlEpoch/controlMode,
controlSafe, phase, materialRevision, auditPassed, samplesIndependent,
inFlightUl, tipsRemaining, wasteFreeUl, lastOperationId, lastAppliedUl,
remainingUl, observationRevision and lastObservedLive/lastObservedDead.
No PlanStatus, InvokeRequest, JSONPath or recording shape changes are needed.
Observe/count records identify the source as `simulated-state-model`, retain the
material revision, vessel/culture/parent, and separate measured/model counts.

Required config is axisJoints[3], pumpJoint, tipSite, pipetteBody,
vessels[{id,entityKey,site,capacityUl,...}], tips[{id,site,bodyKey}]. Configure
managedDevices, imagerStation, wasteSite and vessel lidProperty/requiredStation
for the complete scene. Pump mapping defaults to 100000 uL/metre (1000 uL over
10 mm), tipCapacityUl=1000, alignmentToleranceM=0.003, axisToleranceM=0.0005,
holdSpeedTolerance=0.0001, pumpToleranceUl=0.01. These are fixed engineering
configuration, not a permission to loosen a failed physical qualification.

The teaching recipe uses 200 uL PBS wash, 200 uL trypsin and 2 simulated seconds,
then 800 uL medium for each independent 1000 uL collection. After pelleting,
retain 50 uL residual, add 950 uL medium and mix. Remove 20 uL into the matching
counting well, split the remaining 980 uL as 490/490 uL to its two daughters,
and add 510 uL medium to each. Four samples form the first mass-balanced batch;
the remaining two use the configured two balance tubes in the second batch.
This is a teaching simulation, not a real biological protocol. Actual cycle
load/rotation and every liquid action still need mechanical evidence.

Focused tests: `python -m pytest tests/unit/test_cell_passage.py
tests/gateway/test_cell_passage_api.py -q`. The deterministic external actuator
fixture proves Host/API/recorded-state semantics. It does not prove Newton
geometry, real tip pickup or a full Cell Passage run.
