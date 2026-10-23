# Scene collaboration prototype · revision 2

Throwaway design artifact. No production implementation is promoted to main.

**Question:** Does the prototype clearly separate external conversation and
authoring from the platform's asset, version, verification and execution role?

**User-confirmed boundary:** purposes, reference pictures, dimensions,
clarification, approvals and change requests belong in Claude Code or another
external agent conversation. Agents or people edit Blender work files. Lab World
receives artifacts and explicit reports; it does not host another authoring
wizard, reference upload form, chat client, placement control or modeling form.

## Open

Double-click `scene-factory.prototype.html`, or run:

```text
pnpm prototype:scene
```

The loopback preview is `http://127.0.0.1:5217/?variant=A`.
`SCENE_PROTOTYPE_PORT` selects another port. The HTML works on its own without
dependencies, external fonts, network assets, persistent storage or backend calls.

## Explore the three applications

- **Claude Code:** scripted examples of references, clarification, confirmation,
  repair discussion and change requests. This is an external application mock,
  not an embedded platform chat. No message is sent to a real agent.
- **Blender:** a diagram of the external working file, including the alternative
  where a person edits directly. Changes can exist here before platform import.
- **Lab World:** actual proposed product controls for asset search/acquisition,
  scene/version inspection, validation and starting a verified scene. Summaries
  are read-only and identify the external submitter.

The dark **原型播放器 · 非产品功能** strip is outside all three application
windows. “演示下一段” advances a scripted cooperation event. Switching applications
inspects the same point in the story. Rewinding or changing stories reconstructs
the demonstration snapshot; it is not a product command or live synchronization.

Try “修改一个已交付场景”: after a Blender change, switch to Lab World before the
new import. The platform still shows the old position and verified v2. The later
import produces v3 with its own pending validation. Budget and missing-robot
stories also send decisions back to the external conversation.

The floating A/B/C switcher only compares **platform result presentation**:

- **A — 场景优先:** large received-version preview with metadata and reports.
- **B — 资产与版本:** version inventory and fixed asset dependencies first.
- **C — 接收记录:** submitted events alongside the received scene.

The `variant` URL parameter preserves presentation choice; model data resets on
reload. “查看交付后的平台” jumps to a populated result for layout comparison.
Use the player buttons for story navigation; left/right keys compare platform
layouts when inspecting Lab World. “查看演示状态” explicitly separates the
external workspace state from received platform data.

## Scope of the demonstration

The example uses an FR3, workbench, cart and six-hole rack. Search uses a small
keyword/alias map. Public package names and selected BOM sizes informed the
planning; dimensions, eligibility, reports and execution results are fictional.
SVGs are explanatory diagrams, not imported meshes or rendered Blender output.

No files are uploaded, no geometry is edited and no services are connected.
Copying a report only writes to the clipboard, or presents selectable text if
the browser cannot use the clipboard; the user chooses where to paste it.

Browser inspection covers the three surfaces, unsubmitted-edit isolation,
version-specific validation, blocked launch, normal search/acquisition controls,
mobile layout and standalone file use. No test suite or runtime changes are
part of this prototype.

**Verdict:** the external-dialogue / external-authoring / platform-results
boundary is selected by the user. Revision 1's in-platform authoring wizard is
superseded and remains in commit `f7197ec` for provenance. The A/B/C presentation
winner is still undecided. Production behavior must be implemented separately.
