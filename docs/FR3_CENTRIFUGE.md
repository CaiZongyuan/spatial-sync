# FR3 centrifuge workbench

The platform and received scene have separate repair responsibilities (#212,
#213). `slots` and tube `location` still mean reliably seated occupancy. The
centrifuge also measures whole collision bodies in its rotor region: unsafe or
unconfirmed loading sets `loadSafe=false`, raises `unsafeLoad` after one simulated
second, and rejects `start` with `unsafe_load`. Both action acceptance and actual
startup check current physics. A loose tube outside this region does not prevent
an otherwise empty centrifuge from running. Unknown poses/geometry fail closed.

Newton placement completes only after distinct physical samples span at least
one continuous simulated second of seating and low relative speed. Failure to
settle within the bounded verification phase returns `place_failed`. Plan
`expect` still evaluates its condition immediately; it is not a future guarantee.
Teaching welds use stiffer MuJoCo equalities to prevent a small offset load from
rotating through a soft constraint during the physical demonstration.
The Newton runner checks its control pipe between batches of at most four
physics steps, so catch-up work cannot accumulate a 64-step delay before handling
an action. The existing action-intake deadline remains unchanged.

The received scene needs tapered support in both the rotor and rack. The repair
uses a 130 mm rotor seat radius, re-taught four-slot approach/grasp vectors, rack
bottom inserts and sealed rigid tube proxies. Tube appearance, mass/inertia,
bottom frames and the robot's physical TCP are retained. Moving the rotor Sites
requires the new teaching; old joint vectors must not be reused. The rack's new
rigid body uses a world anchor at its original installation pose. Estimates are
labelled in each package's provenance, with collision and visual geometry updated
together. Original immutable packages remain readable.

## Reproduce and launch

Keep the original received library, including `fr3-image-lab@3` and its original
assets, outside Git. With a gateway serving that same bundle and an environment
with the simulation/USD extras, run from the repository root:

```powershell
uv run --no-sync python tools/repair_fr3_scene.py <received-library> .scratch/fr3-repair --url http://127.0.0.1:8000
```

The tool checks the recursive original dependency hashes on the target before
importing, writes fresh asset packages and imports them through the public API.
It substitutes each returned revision, including the nested rotor reference;
assigned revisions may differ across libraries. Receipts and the actual scene
version are in the fresh output directory's `versions.json`. It never operates
an existing Session. Use that returned version for the local demo:

```powershell
uv run --no-sync lw scene launch <returned-scene-version> --source newton
```

Only one Session can be active on a gateway. To run unattended on an isolated
gateway, create the Session through `POST /api/v1/sessions` with
`continueWithoutObservers=true`; with this option false, losing the final
WebSocket observer intentionally pauses simulation. Repeated `resume` cannot
override that option. Operate through Catalog/state/Plan as described in
`skills/lab-operate/SKILL.md`.

`rack.A2` and `rack.B2` require clearing all four corners first. These source
conditions do not belong to destination `centrifuge.slot2/4`. Move the four corner
tubes to the four rotor slots, then access the middle rack positions and restore
the corners. Refusals now identify `from`, `to` or `location` and retain every
failed condition.

## Qualification boundary

The intended demonstration covers four slots, each with five public
place/10-second hold/retrieve cycles below 5 degrees; opposed loading, a program
3000 rpm / 10 second run, stop/open, ten seconds after weld release and unloading;
and access to both middle rack positions after clearing the corners. Qualification
results and delivered revisions belong in #213. Raw receipts stay in `.scratch/`.

The physical rotor speed target is capped at 60 rpm in the demonstration. This is weld
teaching with estimated properties, without real high-speed centrifugation,
liquid containment, pouring or contact-training qualification. The unsafe-loading
check is a conservative collision AABB check, not exact geometric containment.
