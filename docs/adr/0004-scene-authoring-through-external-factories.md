---
status: accepted
date: 2026-10-23
updated: 2026-10-24
---

# Author scenes in external factories and retain the original Asset references

The first scene-authoring workflow stays outside the platform: people may use Blender for visual layout, while agents may use Blender Python and OpenUSD scripts. The platform provides asset discovery/acquisition, package import, validation, inspection and execution. We chose this boundary over an initial Web scene editor so the existing scene package and validation model can support a complete authoring loop with fewer new editing contracts.

Requirements, reference images, dimensions, clarifications, blockout confirmation and change requests belong in the person's conversation with an external agent client such as Claude Code. Modeling and layout changes are made by that agent through Blender tools or by the person directly in Blender. Lab World provides search, acquisition, asset/scene management, reported progress and gaps, validation and execution. It does not duplicate that conversation with requirement forms, reference uploads, blockout-approval wizards, modeling parameters, placement controls or an embedded authoring chat.

The platform may display a read-only brief, confirmation summary or authoring status that an external agent/factory explicitly submits, with its source and submission time. It cannot infer external progress or observe unsent Blender edits. Its own import and validation results remain distinct from externally reported work; an absent report is unknown, not evidence that a step has started or completed.

Blender is an authoring tool; a published Scene Version remains a USD composition of fixed Asset versions. Existing validated robots and physical Assets retain their original packages. The factory extracts instance identity and placement from the authoring workspace and writes references and layout opinions, rather than treating a Blender import/export round trip as preservation of the original physics. Blender's documented USD layer, variant and material limitations make that distinction necessary. Changed geometry becomes a derived Asset with new provenance and validation.

Use MCP for Blender as the agent's Blender control layer and small project-owned scripts for asset identity, layout extraction and package production. Scene-critical unknowns and placeholders remain explicit in a Scene Draft. A model's visual appearance does not establish its physical or behavioral suitability for the requested task. Image-to-3D is an optional route for noncritical props after reuse and procedural modeling, with a separately selected service, license policy and generation budget.

This records an accepted design; the reusable factory and Blender bridge are not yet implemented. The workflow and verification targets are in [Lab Scene Factory](../spec/LAB_SCENE_FACTORY_SPEC_V1.md).
