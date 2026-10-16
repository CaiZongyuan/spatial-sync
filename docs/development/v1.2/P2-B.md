# P2-B subscriptions and keyframes

The stream/consumer implementation is frozen at `4bca2acffef4a17412c8f1242082d8b703bb4058` on `issue/p2-b-subscriptions`, including root `c7867ee`. The initial issue baseline was `b76a78213d29a77e89ca21f5fcf9f1c1fe3c9a4a`; source/binary/cadence changes were coordinated with P2-A. This report records local delivery, not an integrated acceptance claim. Actual mesh/instance visibility and raycast exclusion are a P2-C integration dependency, described below.

`all`, `entities`, `device` Group and physics Z-up `viewport` scopes now reach both JSON and binary consumers. Logical descendants, Site parents and both Joint Bodies resolve from the Manifest. Viewport clipping tests conservative visual/collider bounding spheres, including local offsets, and retains unknown GLB/mesh bounds. HTTP snapshot and pose endpoints continue to return full source data.

Deltas are cumulative relative to an explicit full `baseSeq`. Returning to the baseline pose, dropped intermediate deltas, entering then leaving members, removals, reset/revision changes and reconnect are covered. Reliable regular keyframes rebase without clearing interpolation; recovery/reset snapshots clear timing. Applied command frames remain full and reliable before requester ack and broadcast state. Reliable FIFO is preserved through rapid subscription changes; only the periodic latest slot is discarded. Request IDs correlate subscription acks, and subscription IDs reject obsolete frames.

The mutable typed PoseBuffer stores membership separately from poses. `readLatest` and displayed readers return unavailable for excluded Bodies; new members snap to their current pose rather than interpolating through an old scope. Inspector retains selection and displays an explicit unavailable message. The viewport camera bridge maps Three Y-up plane coefficients `(nx,ny,nz,d)` to physics `(nx,-nz,ny,d)`, checks only changes and throttles updates to 100 ms. The axis mapping has an independent half-space test. Scope controls expose Group, selected entity, viewport and sparse/full streaming.

## Verification

- Final code: **128 Python passed**, with one optional mesh-export module skipped because Newton is absent. This is not physics/GPU validation. [Raw receipt](P2-B-python-validation.txt).
- **63 Web tests**, **6 TypeScript protocol tests**, production build, generated-artifact consistency and Ruff passed after the final simplification. Existing Vite bundle-size and Starlette test-client deprecation notices remain advisory.
- Existing real Synthetic workbench plus JSON/binary shared-control/reconnect/revision E2E: **2 passed, 24.1 s**. No fabricated browser/source poses were used.
- New scoped JSON and binary E2E: **2 passed, 25.5 s** on the final candidate. [Raw receipt](P2-B-browser-validation.txt), [JSON wire counts](p2-b-evidence/json-wire.json), [binary wire counts](p2-b-evidence/binary-wire.json).
- Each format deliberately lost four intermediate periodic deltas. The browser's accepted sequence and all Body positions matched an independent reconstruction from the actual gateway packets. Rapid requests settled on the latest scope, the selected non-origin Tube became unavailable under Robot-only scope, and actual mouse camera changes produced viewport requests which stopped when damping settled.
- One deliberately lost regular full packet caused exactly one unknown-base resubscription; actual upstream disconnect then recovered through a new full snapshot. The binary run received **80 deltas and zero JSON pose frames**. The JSON run received 62 deltas. These are functional observations, not throughput benchmarks.
- A separate actual moving SyntheticSource WS test, for both formats, clips tubes across a physics Z plane. It discards alternate deltas and compares reconstructed membership with independently calculated cylinder bounds at each actual `simTime`. It proves changing membership without relying on origins alone.
- Actual blocked pump, public `observer_subscribe` and another client's applied command prove old reliable frame/state precede both rapid ack/snapshot barriers. Mixed legacy/scoped first subscriptions of the same format prove cache reuse cannot confuse binary kind 1 with kind 4.
- With `core.autocrlf=true`, an independent `checkout-index` produced LF-only TS, JSON Schema and three representative fixtures. The byte-determinism test was retained. GLB files have a binary attribute.

PM independently read the high-water oracle and scoped consumer regressions, and ran 11 focused gateway tests and 30 focused consumer tests on checkpoint `96608b0` (the merge after `46b2bf1`), reporting no blocker. Final post-checkpoint changes are the camera mount/helper test, public moving-scope/browser evidence, removal of redundant connection state, and avoiding unrelated tree selections triggering resubscription. Final review/integration remains PM-owned.

## Findings and bounded simplification

The observed-sequence reuse defect was first reproduced against a real 1 Hz source, then repaired with an independent allocation watermark; original delayed command metadata stays honest. [Counterexample and evidence](P2-B-high-water.md).

A new non-origin exit/re-entry test first failed because interpolation could reach a pose from before an entity left the scope. Per-member entry sequences now prevent that path. PM also found and the author repaired two concrete counterexamples: legacy/scoped cache-key collision, and invalid delta data reverting to the baseline rather than retaining the previous valid pose. JSON/binary regression tests preserve both oracles.

The initial scoped E2E used a fixed 1.8 s delay to assume OrbitControls had stopped damping and failed in both formats. Actual planes were still changing. The final test waits for an observed unchanged interval; it does not weaken the camera on-change contract. The first attempted protocol tests also identified stale generated fixtures before regeneration, rather than a behavioral failure.

A bounded simplification removed unused duplicated `ClientConnection.stream_ids` and unused reliable-generation tagging. The resolved ScopeFilter is the sole selection owner; generation filtering applies only to periodic deliveries. Scope resolution and cumulative-baseline logic remain separate modules because topology/bounds and temporal reconstruction have different invariants. No generic transport abstraction or extra runtime dependency was added.

## Integration handoff and resources

P2-C owns applying `buffer.isMember(streamId)` to actual objects/instances and raycasts. D confirmed attached Sites follow their parent Body membership, selected Joints use the child anchor while Inspector also requires the parent's current pose, and World Sites use the agreed all/explicit-Site rule. The screenshots [JSON](p2-b-evidence/json-excluded-inspector.png) and [binary](p2-b-evidence/binary-excluded-inspector.png) demonstrate Inspector exclusion and stable selection only: the pre-C canvas still shows excluded meshes. They must not be cited as rendering visibility acceptance. Root must close this cross-issue condition after the C renderer is integrated and verified.

`CameraSubscription` is mounted in SceneContents with D's authorization. D should merge the final B branch once its independent renderer baseline completes. It must preserve the request/scope/baseline barriers while adding rendering integration.

Verification used isolated API `8007` (last PID 27156) and web `5187`, with explicit allowed Origin. Both owned terminal sessions were stopped and both listeners were verified absent. Every E2E-created Session was deleted in `finally`. Root `8000/5173`, C `8006/5186`, and unrelated services were not stopped. No GPU work ran. No retained B service, Runner or browser process is required.
