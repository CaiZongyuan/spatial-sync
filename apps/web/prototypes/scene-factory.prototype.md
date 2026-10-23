# Scene factory interaction prototype

Throwaway design artifact. Do not merge this prototype into the product or treat
its in-memory model as production behavior.

**Question:** Can a person understand and direct the complete scene-building
journey—requirements, retrieval, blockout approval, missing-asset creation,
composition, validation and reuse—while seeing what the agent can do autonomously?

## Open

Double-click `scene-factory.prototype.html`. It is one self-contained HTML file:
no dependencies, external fonts, network assets, storage or backend calls.

For a local URL, run from this branch/worktree:

```text
pnpm prototype:scene
```

Then open `http://127.0.0.1:5217/?variant=A`. Set `SCENE_PROTOTYPE_PORT` to use
another port. The server binds only to loopback.

## Explore

- **A — 场景工作台:** scene-first workspace and persistent agent/state context.
- **B — 引导式导演:** conversational decisions with a smaller persistent scene.
- **C — 全局流程板:** all seven stages and responsibilities visible together.

Use the floating switcher or left/right arrow keys. The `variant` URL parameter
persists presentation choice on reload; scenario data always resets. Arrow keys
are left to inputs and editable fields while they have focus.

“带我走一遍” starts a guided journey; subsequent clicks execute individual
prototype actions. All stages also support free exploration. Choose the budget,
unknown-metadata, missing-robot or collision-failure scenarios to inspect gaps.
User/agent views share the same candidate data and constraints. “状态” exposes
the readable state and optional JSON; “交付规划” separates future capabilities
from this mock implementation.

The task is deliberately fixed to an FR3, workbench, cart and six-hole rack.
Editable brief text illustrates the interface; it is not interpreted by an LLM.
Natural-language retrieval uses a small deterministic keyword/alias mapping.
Source package names and some BOM sizes are based on the planning investigation;
dimensions, eligibility, validation and execution results are fictional demo data.

The SVGs are diagrammatic illustrations, not actual imported geometry, Blender
renders, motion planning or Newton results. Optional reference images are read
into browser memory only. Export writes an explicitly marked prototype JSON
summary to the user's downloads; nothing is sent to a service.

## Observations and status

Browser walkthroughs exercised the complete guided flow and the key boundaries:
unknown fields do not meet hard constraints; oversized downloads are rejected;
missing trusted robots require an explicit choice; failed collision blocks
delivery; fixing it creates a new scene/asset version and keeps the failed one.
Changing requirements, assets or layout invalidates downstream confirmation.
Offline simulation blocks new acquisitions, while imported data remains usable.

The standalone file was opened through `file://` with the browser offline and
remained interactive. Desktop variants and a mobile viewport were inspected.
No automated test suite or production runtime was added or run for this artifact.

**Verdict:** the flow and alternatives are ready for user comparison. No layout
winner or production design has been selected. No prototype code has been
promoted to main. The branch is the primary-source record for later discussion.

Planning basis: the user-approved external-assets and Scene Director discussion,
including the NVIDIA-first catalog, 512 MiB per-package / 2 GiB batch / 10 GiB
cache limits, fixed versions, separate visual/physical claims, external factories
and MCP for Blender as a control layer.
