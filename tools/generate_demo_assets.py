"""Generate the small, original Y-up equipment-cover fixture with locked trimesh."""

import sys
from pathlib import Path
from types import SimpleNamespace

import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.sim_runtime.mesh_assets import MeshStore


def main():
    mesh = trimesh.creation.box(extents=(0.6, 0.6, 0.4))
    mesh.apply_translation((0, 0.3, 0))
    store = MeshStore()
    mesh_id = store.register(SimpleNamespace(vertices=mesh.vertices, indices=mesh.faces))
    data = store.read(mesh_id)
    target = ROOT / "assets/visuals/equipment-cover.glb"
    if "--check" in sys.argv:
        if target.read_bytes() != data:
            raise SystemExit("Demo GLB differs from its source; run this script without --check")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(f"{target.relative_to(ROOT)}: {len(data)} bytes, {mesh_id}, authored Y-up")


if __name__ == "__main__":
    main()
