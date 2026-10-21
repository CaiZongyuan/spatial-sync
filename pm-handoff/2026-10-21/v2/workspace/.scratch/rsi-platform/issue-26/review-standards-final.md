# Standards review — issue 26

Candidate: `26c6e8f998d4c0d316ce73b4fc4992709c303fd9`, base `21417c855b5f7855525ebf28d76ae350f6f36541`.

Reviewed the complete 25-file task diff, including the plan model/store/hooks, live and replay stream consumers, shared checklist/detail/session integrations, Manifest-keyed renderer extraction and ghost ownership, routing, styles and tests. The original owner implemented this work; this is a non-author review by the coordinating agent, which shares session context. Spec review remains separately assigned to the returning independent reviewer.

No blocking documented-standards violation found. Web reaches the existing HTTP pipeline; replay disables metadata and preview reads and uses recorded plan reducers. Protocol and recording versions, geometry conversion conventions, source state and device definitions are unchanged. The renderer extraction has two actual consumers, ordinary scene bodies and ghost bodies; cloned ghost materials preserve live material ownership. Async hooks cancel stale work and scope results to the current revision/epoch. Historical status and adjacent-revision repairs do not fabricate missing information.

Complexity observations: the new page and checklist are dense JSX, but their responsibilities remain distinct and this alone does not justify another abstraction. No optional refactor is a delivery gate. Existing focused validation and real journey evidence are documented in CHECKPOINT.md / REVIEW-REPAIRS.md; this source review does not replace exact-head CI or verify an unperformed native policy execution.

On baseline movement, refresh the semantic integration delta, especially SceneViewport/NodeVisuals and the Workbench rail shared with issue 86. Final CI and the source/tree match at integration remain pending.
