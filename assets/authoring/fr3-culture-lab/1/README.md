# Image/prompt laboratory authoring source

The user supplied this project and requested its assets be committed and merged
in #214. `laboratory_final.blend` is the unchanged editable Blender source;
`reconstruction.zip` retains all nine original scripts, source manifests,
official FR3 meshes/kinematics and upstream notices. `reference.png` and
`scene-director.md` retain the original visual brief. Original byte hashes are
in `source-manifest.json`.

The `.blend` was authored in Blender 4.3.2. The bounded derivation used Blender
4.5.9 with automatic script execution disabled. Original scripts are retained
for editing/reconstruction and are not executed by the gateway or CI.

The source uses procedural/constant materials and Cycles lighting; it contains
no delivered image-texture bake. The built-in `fr3-culture-reference@1` is a
static, cutaway visual derivative. Ceiling, photographic outdoor backdrop and
volume are omitted. Area-light/global-illumination reproduction, interaction,
collision and training qualification are not part of that version.

To derive fresh staging packages from the retained source:

```powershell
<blender-4.5.9> --background --factory-startup --disable-autoexec --python-exit-code 1 --python factory/reference_lab_blender.py -- --source assets/authoring/fr3-culture-lab/1/laboratory_final.blend --output .scratch/fr3-reference/derived
uv run --no-sync python tools/package_reference_lab.py --derived .scratch/fr3-reference/derived --output .scratch/fr3-reference/library
```

Derived files have their own hashes and versions. Rebuilding is not assumed to
be byte-identical across Blender releases. See `CONTRIBUTION_NOTICE.txt` for
the contribution scope and `FR3_LICENSE`/`FR3_NOTICE` for upstream terms.
