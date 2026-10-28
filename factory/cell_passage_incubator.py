"""Synchronize the simulation's single-level incubator visual and collision derivative."""

import hashlib
import json
import shutil

import numpy as np
import trimesh
import yaml


def build(source, output):
    original = source / "assets/verified-world-lab-incubator-base"
    target = output / "assets/device/passage-incubator-single-level/1"
    shutil.copytree(original, target)
    visual = trimesh.load(original / "visual.glb", force="scene")
    removed, kept = [], trimesh.Scene(base_frame="world")
    for index, (name, geometry) in enumerate(sorted(visual.geometry.items())):
        lower, upper = geometry.bounds
        shelf = (
            np.allclose(lower[:2], [-0.261, -0.199], atol=0.0001)
            and np.allclose(upper[:2], [0.261, 0.229], atol=0.0001)
            and 0.39 < lower[2] < upper[2] < 0.71
        )
        holes = (
            np.allclose(lower[:2], [-0.259, -0.197], atol=0.0001)
            and np.allclose(upper[:2], [0.259, 0.227], atol=0.0001)
            and 0.39 < lower[2] <= upper[2] < 0.71
        )
        if shelf or holes:
            removed.append(name)
            continue
        nodes = [node for node in visual.graph.nodes_geometry if visual.graph[node][1] == name]
        if len(nodes) != 1:
            raise ValueError("Pinned incubator component instancing has changed")
        matrix, _ = visual.graph[nodes[0]]
        kept.add_geometry(geometry, geom_name=name, node_name=f"Component{index:03d}", transform=matrix)
    if len(removed) != 6:
        raise ValueError("Pinned visual must expose exactly three upper shelf/holes pairs")
    (target / "visual.glb").write_bytes(kept.export(file_type="glb"))
    metadata = yaml.safe_load((target / "asset.yaml").read_text(encoding="utf-8"))
    metadata.update(
        id="device/passage-incubator-single-level", title="Simulation single-level incubator attachment"
    )
    metadata["description"] += (
        " Derived simulation configuration: three upper shelves removed in visual and composed collision;"
        " no OEM modification claim."
    )
    (target / "asset.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    provenance = json.loads((target / "provenance.json").read_text(encoding="utf-8"))
    provenance["derivative"] = {
        "sourceAsset": "device/verified-world-lab-incubator-base@1",
        "sourceVisualSha256": hashlib.sha256((original / "visual.glb").read_bytes()).hexdigest(),
        "removedVisualGeometry": removed,
        "configuration": "Simulation single-level derivative; no manufacturer hardware-modification approval",
        "collisionRecipe": "Composed stage removes Collision_shelf_2/3/4 and their hole surfaces",
    }
    provenance["files"] = {
        str(path.relative_to(target)).replace("\\", "/"): {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(target.rglob("*"))
        if path.is_file() and path.name != "provenance.json"
    }
    (target / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return target
