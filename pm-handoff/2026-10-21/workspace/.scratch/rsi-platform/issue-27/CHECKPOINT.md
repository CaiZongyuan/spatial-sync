# Issue 27 first-phase checkpoint

Base: `5c29ca3bbc0495f868bc2bbe89ebb4cf53410f61`.
Clean WIP HEAD: `266d7b337bcccf333603636158521611d53350b3`, tree `0e12fa4184d77e7299a53c3bbed95b73eed4f036`.
Branch/worktree: `feat/issue-27-device-host`, primary `.worktrees/issue-27`.
One owner, 10 task files; no writes to issue25 or primary tracked files.

## Implemented bounded seams

- Extracted existing Catalog compilation for a nonspatial second consumer, retaining the scene resolver and publication shape.
- Optional per-entity driver mailbox on the same Ctx/HostCore; normal Sources retain their existing availability behavior.
- Generic optional availability result checked by ActionPipeline and authoritative HostCore before conditions. The first real case proved this is necessary: otherwise an offline device with stale temperature returns `precondition_failed`, violating issue27's `device_unavailable` contract.
- Bounded mailbox and actual incubator, biosafety cabinet and environment MQTT Programs. All callbacks are synchronous; command staging is queued, not network I/O. Telemetry must pass property validation before freshness or online state changes. Matching ack, topic, identity, active command and deadline control completion. Disconnect before start prevents command staging; fresh non-retained telemetry is required after reconnect.

## Actual lightweight execution

Own worktree `.venv` did not exist; inventory found no own Python consumer. `uv sync --frozen --group dev` installed 53 frozen base/dev packages in the new own environment only; no Newton simulation extra or new dependency installed.

1. First `pytest tests/unit/test_live_host.py -q`: red, 1 failed/0.72s, fixture omitted required `InvokeRequest.createdSimTime/createdAtMs`. Only fixture corrected.
2. Second same test: red, 1 failed/0.30s, reached offline/stale admission and observed `precondition_failed` instead of required `device_unavailable`. Added the narrow availability boundary approved by root.
3. Small counterexample set: 6 passed/0.35s. Targeted Ruff found 3 formatting issues; fixed them. Also corrected ack removal so an offline ack cannot leave an active command without a pending failure path.
4. Final focused command `uv run --no-sync python -m pytest tests/unit/test_live_host.py tests/unit/test_info_host.py tests/unit/test_info_host_publication.py -q`: **21 passed in0.67s**, actual exit0. Seven new tests cover real HostCore/ActionPipeline behavior with socket-free mailboxes; existing host/publication tests preserve callback and ordering consumers.
5. Targeted Ruff over every changed Python file: **All checks passed**, exit0. `git diff --check` passed. No extra identical green repeat.

No API, Session/Source, broker, browser, Newton, performance or long integration process was launched. Final own `.venv` process inventory empty. Protected primary staged HTML remains SHA256 `D5A4A5AC23350C84238D8871B62A5253B5ADDA294EBAFF9A9A4FB1A037FCBBEE`.

## Continuation

Root requested this clean checkpoint to borrow the owner for issue83 documentation review and prioritize CI capacity. Resume issue27 from this same worktree; no other owner should write here.

Still required: typed live binding loader and layout/placeAt metadata; gateway-owned monotonic DeviceHost lifecycle; production MQTT client adapter and documented JSON contract; shared `/live` routes and approval target resolution; generic CLI/MCP routing; 3-device mock fault tool; actual injected-broker gateway tests and actual owned local mosquitto proof where available. Do not present the current mailbox-only tests as production MQTT validation.

Retained/duplicate/late identity counterexamples pass. Outstanding transport-level checks include timestamps recorded at actual receipt vs processing tick, acks before dispatch, old queued commands on cancellation, status-topic ordering, malformed JSON, reconnect generation, telemetry before initialized owner, and bounded queue pressure. Confirm no MQTT callback mutates HostCore/StateStore on the client thread. Live high-risk actions must use existing `require` approvals from their first exposed route. No unsafe alternate intake while issue28 recording/history/inbox/UI integration is pending.

Issue83 now governs final delivery: latest main push green; rebase current main and exact-head CI; complete all four Newton CPU commands because `packages/programs/**` changes; document commands/results/tick P95 in PR. Linux/WSL is the budget baseline, Windows reference only. Strict source-input captures are stale after shared producer edits; coordinate actual recaptures after final freeze. No merge before root's docs-first maintenance integration. No timeline.
