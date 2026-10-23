# Diagnostic source snapshots for PM handoff

This preservation branch contains byte-for-byte copies of local diagnostic source
and input manifests. It does not change the application or install dependencies.
`restore-map.json` maps each file to its original checkout-relative `.scratch/`
path and records its SHA256. Keep the original files and failed attempts intact.

The current product baseline is `f9f58650b471f7330714325ec6bcf55d1dcfc24e`.
Issue #127 is a separate proposed execution-plan update; this snapshot predates
its implementation. Read the PM handoff issue linked from #2 for current status.

## Sources

- `issue-18/`: frozen C0 adapter/data helpers/input manifests, original r3 helper,
  the failed launcher source, and two **unapplied** receipt-repair diffs. C0's two
  launcher attempts both ended before Model/FK. The repair is not implemented.
- `issue-102/`: Synthetic v4 instrumentation and its base probe/support, overlay
  manifests and offline readers. The qualified v4 run still failed 36 of 39
  latency budgets; this is not a product dependency change or a performance fix.
- `issue-85/`: existing CI/profile offline analysis sources. The candidate itself
  lives on `fix/issue-85-ci-margin-refresh` (Draft PR #126).
- T0 D2 source is separately preserved on `spike/issue-29-round2`, commit
  `6696ad77019014dfb8278b83feec54ebde1f9c65`, under
  `experiments/fr3_contact_round2/`.

## Restore prerequisites

Copies retain original absolute paths and imports. Merely checking out this
branch does not create a runnable experiment. Restore copies to the mapped
scratch paths only in a suitable task-owned workspace; do not overwrite existing
evidence. Rebinding a path changes the candidate and needs a new manifest.

C0 additionally needs the private mechanical-A scene/asset package at
`.scratch/rsi-platform/issue-18/factory/mechanical-a-001/private-seed-packages-003-adopted-003/`,
the runtime checkout `3e3d7bcae567348dda5a60e9e51f3c556688b052`, Teacher checkout
`8db36c375d6e6d0da674df091ea4c0fc6a88efa0`, and their pinned Python environments.
Those assets/environments are not bundled here; `PLAN.json` names and hashes the
inputs. The same machine retains them. A fresh machine must recover them first.

Synthetic v4 needs the read-only Linux baseline environment and product checkout
`8db36c375d6e6d0da674df091ea4c0fc6a88efa0`, the unchanged original 79 package pins,
the paho wheel and the exact official sniffio 1.3.1 wheel. Their identities and
expected unpacked image are in the manifests. Wheels, prepared Linux contexts,
run outputs and existing authorizations are not bundled. The sniffio wheel SHA256
is `2f6da418d1f1e0fddd844478f41680e794e6051915791a034ff65e5f100525a2`.

CI analysis needs the actual GitHub attempt artifacts and current collection
maps. Profiling analysis needs the local captured profiles. Raw receipts,
profiles, journals and benchmark output remain in `.scratch/`, not this branch.
No historical GO or reservation is transferred by these source copies.
