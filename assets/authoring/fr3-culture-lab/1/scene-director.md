blender-scene-director&#x20;

Orchestrate and build high-quality, photorealistic 3D Blender scenes by combining Blender Python (bpy) procedural modeling with 2D-to-3D AI asset generation, mesh/PBR refinement, hierarchical scene dressing, and volumetric lighting. Use when the user asks to generate, design, script, or build complex 3D environments, rooms, interiors, or scenes in Blender using AI or Python scripts.

#### Instructions

# Blender Scene Director

Guide the agent to act as a 3D Technical Artist and Scene Director, building complex, production-grade Blender scenes by combining exact Python (`bpy`) procedural modeling with AI-generated 3D assets and targeted mesh/material refinement.

## When to Use

- The user wants to create a detailed 3D room, interior, or environment in Blender using AI or Python scripts.
- The user needs an orchestration workflow that combines `bpy` scripting with external 2D-to-3D generation tools (such as Tripo, Meshy, or Rodin).
- The user asks how to fix uncanny, melted, or poorly shaded AI-generated 3D assets inside a Blender pipeline.
- The user wants to automate scene dressing, shader node creation, or multi-mood lighting setups in Blender.

## Steps

### Phase 1: Visual Anchor and Structural Blockout (`bpy`)

Never generate the entire scene as a single raw AI mesh. Establish accurate spatial proportions and crisp architectural lines through code first.

1. **Generate a Concept Anchor**: Create or request a 2D concept image to serve as the master reference for spatial layout, color palette, clutter density, and lighting direction.
2. **Script the Architectural Shell**: Write `bpy` scripts to build walls, floor planks, baseboards, window frames, and doors with real-world metric dimensions.
3. **Script Primary Hard-Surface Furniture and Mechanisms**: Build load-bearing furniture (desks, shelving units, cabinets) and articulated mechanical elements (such as window blinds with individual slats, top rails, cords, and pulleys) directly in Python so surfaces remain flat and intersections stay clean.

### Phase 2: Intelligent Asset Triage

Break down the required scene props into an asset manifest and route each item to the appropriate creation pipeline based on geometric regularity.

1. **Route to Direct Procedural Modeling (`bpy`)**:&#x20;
   - **Criteria**: Geometric primitives, sharp edges, thin flat items, cables, or assets requiring parameterized instancing.
   - **Target Assets**: Books, magazines, folders, cardboard boxes, pens, batteries, monitor arms, AC ducts, rugs, tape rolls, stickers, and draped cables (using Bezier curves with bevel depth).
2. **Route to 2D-to-3D AI Generation + Refinement**:&#x20;
   - **Criteria**: Organic silhouettes, soft goods, sculpted characters, crumpled topology, or intricate everyday objects.
   - **Target Assets**: Padded office chairs, vacuum cleaners, folded towel piles, plush toys, handbags, tangled charger cords, crumpled snack bags, and figurines.
   - **Execution**: First generate a clean, isolated white-background reference image of the single prop, then feed it into an image-to-3D generator to produce the base textured mesh.

### Phase 3: AI Asset Refinement and Repair

Raw 3D AI outputs often suffer from uniform plastic roughness and melted micro-details. Always apply a refinement pass in Blender before final placement.

1. **Targeted PBR Material Correction**:&#x20;
   - Inspect generated textures and build a custom Shader Node graph or vertex/texture mask in `bpy`.
   - Separate material zones that require distinct physical responses. For example, on a torn foil snack bag, keep the printed exterior matte while driving high Metallic (`0.8 - 1.0`) and low Roughness (`0.15 - 0.3`) exclusively on the exposed silver inner lining.
2. **Hybrid Mesh Reconstruction (UV-Preserving)**:&#x20;
   - Identify high-frequency mechanical details on generated props that look blurry or melted (such as speaker grilles, radio dials, knobs, or screen bezels).
   - Write a `bpy` script to boolean-cut or mask out the degraded region and generate crisp procedural geometry (e.g., an array of grille slats or a beveled dial) in its exact local coordinates, while preserving the original UV map and baked textures on the main chassis.

### Phase 4: Hierarchical Scene Dressing

Populate the scene from macro to micro scale using Python placement scripts with collision/bounding-box awareness and subtle transform randomization.

1. **Primary Anchors**: Place hero props (office chair, PC tower, monitors) relative to the structural furniture.
2. **Secondary Functional Clusters**: Populate shelves and desk zones with medium props (storage boxes, audio gear, bags, stacked books).
3. **Tertiary Clutter and Micro-Storytelling**: Scatter small everyday items (pens, open wrappers, mugs, loose cables, stickers) with slight random Z-axis rotation (`random.uniform(-0.3, 0.3)` radians) to break CG perfection and create lived-in realism.

### Phase 5: Procedural Shaders and Multi-Mood Lighting

1. **Procedural Surface Wear and Glass Shaders**:&#x20;
   - Build node-based shaders via `bpy.data.materials` that add subtle edge wear, dust accumulation (using Ambient Occlusion or Normal Z-up masks), and physically accurate glass transmission/IOR for windows and displays.
2. **Daylight + Volumetric Atmosphere**:&#x20;
   - Configure a Sky Texture or Sun lamp angled through windows to cast strong directional shadows across the room (e.g., through window blinds).
   - Add a World or Domain `Principled Volume` node with low density (`0.005 - 0.02`) and slight anisotropy (`0.3 - 0.6`) to create natural god rays and atmospheric depth.
   - Add subtle emission shaders to computer monitors and LED indicators.
3. **Nighttime Variant with IES Profiles**:&#x20;
   - Create a separate lighting collection or scene state for night renders.
   - Disable or dim daylight sources and activate practical interior fixtures (desk lamps, floor lamps, ceiling lights).
   - Attach **IES light profiles** (`ShaderNodeTexIES`) to point and spot lights to produce authentic, physically measured light falloff patterns on nearby walls and desks, balancing warm practical lamps against cool monitor glow.

## Gotchas

- **Avoid End-to-End Scene Generation**: Never attempt to generate an entire room as a single 3D mesh; walls will warp and props will fuse together. Always separate structural code from prop generation.
- **Avoid Raw AI Mesh Placement**: Dropping raw 2D-to-3D meshes directly into Cycles without adjusting Roughness/Metallic channels will make the scene look like painted clay.
- **Prevent Floating or Intersecting Clutter**: When scripting prop placement, always query object bounding boxes (`obj.bound_box`) or raycast downward (`scene.ray_cast`) onto target surfaces so items sit flush on desks and floors.