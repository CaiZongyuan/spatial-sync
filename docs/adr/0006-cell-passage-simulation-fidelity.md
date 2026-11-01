---
status: accepted
date: 2026-10-28
---

# Separate mechanical facts from modeled experimental state in Cell Passage

The first complete Cell Passage simulation uses Newton for actual geometry, motion, grasp alignment, transport, placement and device loading. Liquid volumes, cell quantities, culture relationships and processing conditions use an explicit experimental state model; observation and counting actions return results consistent with that model and identify them as simulated. The user accepted this scope for the complete adherent-cell passage cycle rather than requiring particle fluid simulation, cell growth mechanics or a cell-image recognition model.

A grasp may use a fixed constraint only after actual reach, alignment, finger closure and grasp geometry pass. Transport must remain continuous, release must work and collisions must be checked. This keeps the complete laboratory sequence achievable with the existing weld foundation while preserving separate requirements for a controller that relies on contact and friction. A fixed constraint cannot repair incompatible geometry or reach, and this simulation's result does not establish contact-controller qualification.

Both mechanical observations and experimental state changes must be visible in the run's evidence. Pause freezes simulation time; cancellation and human intervention preserve material changes that already happened. See [Cell Passage Simulation](../spec/LAB_CELL_PASSAGE_SPEC_V1.md) for the selected route and remaining design decisions.
