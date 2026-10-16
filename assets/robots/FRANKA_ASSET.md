# Official Newton Franka FR3 asset

The `franka_emika_panda` package is copied from the exact asset revision pinned by Newton
1.7.0: `f8fb7abcbeba2318814a74f3eeb02780ad7925d6` in
https://github.com/newton-physics/newton-assets. Despite the package name, its URDF is the
Franka Research 3 arm with seven revolute joints and a two-finger prismatic gripper.

Only the URDF's referenced meshes plus its original LICENSE and README are included:
22 files, 27,420,151 bytes. No files were rewritten. `provenance.json` records every
upstream relative path, byte count and SHA-256. The original per-asset Apache 2.0 license
and attribution are retained inside the package. `tools/vendor_franka_asset.py` reproduces
the pinned developer-time download; runtime scene loading reads only this bundled folder.

`robot-and-tubes` imports `urdf/fr3_franka_hand.urdf` through the real Newton `add_urdf`
API, composes it using `add_builder(label_prefix="World/Lab/RobotA")`, and attaches a
Newton TCP Site 0.1034 m from the hand origin. Joint motor targets drive the authoritative
Newton State. It does not animate rendered links directly.

DAE visuals use pycollada 0.9.3. SciPy 1.18.1 supplies trimesh's sparse normal calculation;
both are optional simulation dependencies, leaving the default gateway environment free
of the physics engine and robot import packages.
