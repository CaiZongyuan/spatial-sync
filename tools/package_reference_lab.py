"""Package the static image/prompt laboratory visuals for the built-in library.

Inputs are the controlled Blender derivation and the retained source contribution.
This packages visual Dressing only; it does not derive robot physics or behavior.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import trimesh
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.library.visuals import glb_budget


def fingerprint(path):
    value = path.read_bytes()
    return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}


def build(derived, source, output):
    recipe = json.loads((derived / "derivation.json").read_text(encoding="utf-8"))
    if recipe["sourceSha256"] != fingerprint(source / "laboratory_final.blend")["sha256"]:
        raise ValueError("Derived visuals do not match the retained source")
    references = []
    for chunk in recipe["chunks"]:
        identifier = "fr3-culture-" + chunk["name"]
        kind = "robot" if chunk["group"] == "robot" else "environment"
        package = output / "assets" / kind / identifier / "1"
        if package.exists():
            raise ValueError("Use a fresh output; asset versions are immutable: " + str(package))
        package.mkdir(parents=True)
        visual = derived / chunk["visual"]
        if fingerprint(visual)["sha256"] != chunk["sha256"]:
            raise ValueError("Derived visual changed: " + chunk["visual"])
        shutil.copyfile(visual, package / "visual.glb")
        glb_budget(package / "visual.glb")
        bounds = trimesh.load_scene(visual, process=False).bounds
        metadata = {
            "id": kind + "/" + identifier, "kind": kind,
            "title": "FR3 cell-culture laboratory · " + chunk["name"],
            "description": "Static visual contribution from the image/prompt Blender scene. "
                           "No collision, device behavior or robot-control qualification.",
            "stage": "asset.usdc", "visual": "visual.glb", "dressing": True,
            "dimensionsM": (bounds[1] - bounds[0]).tolist(),
        }
        (package / "asset.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
        shutil.copyfile(derived / chunk["stage"], package / "asset.usdc")
        shutil.copyfile(source / "CONTRIBUTION_NOTICE.txt", package / "CONTRIBUTION_NOTICE.txt")
        if kind == "robot":
            shutil.copyfile(source / "FR3_LICENSE", package / "LICENSE")
            shutil.copyfile(source / "FR3_NOTICE", package / "NOTICE")
        else:
            shutil.copyfile(source / "CONTRIBUTION_NOTICE.txt", package / "LICENSE")
        provenance = {
            "recipe": recipe["recipe"], "blender": recipe["blender"],
            "source": "assets/authoring/fr3-culture-lab/1/laboratory_final.blend",
            "sourceSha256": recipe["sourceSha256"], "sourceObjects": chunk["objects"],
            "triangles": chunk["triangles"], "coordinates": recipe["coordinates"],
            "approximations": recipe["approximations"],
            "excluded": recipe["excluded"],
            "licenseScope": "Original Franka license applies to robot source geometry only; "
                            "laboratory contribution permission is in CONTRIBUTION_NOTICE.txt",
            "files": {p.name: fingerprint(p) for p in sorted(package.iterdir()) if p.is_file()},
        }
        (package / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
        references.append((identifier, kind))
    scene = output / "scenes" / "fr3-culture-reference" / "1"
    scene.mkdir(parents=True, exist_ok=False)
    documents = {
        "scene.yaml": {"name": "fr3-culture-reference", "title": "图片与 Prompt 制作的细胞培养实验室",
                       "description": "完整 Blender 场景的静态视觉派生物。机器人、器皿与仪器均为布景；"
                                      "无设备操作、碰撞或训练资格；区域灯、体积及 Cycles 全局光照未烘焙。",
                       "sources": ["newton"], "stage": "stage.usda",
                       "simulation": {"solver": "xpbd", "warmupSteps": 1},
                       "derivedFrom": {"factory": recipe["recipe"], "sourceSha256": recipe["sourceSha256"]}},
        "devices.yaml": {"scene": "fr3-culture-reference", "entities": []},
        "locations.yaml": {"locations": {}},
    }
    for filename, value in documents.items():
        encoded = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        (scene / filename).write_text(encoded, encoding="utf-8", newline="\n")
    dressing = "\n".join(
        f'  def Xform "Part{index:02}" (references = '
        f'@library:assets/{kind}/{name}/1/asset.usdc@</Asset>) {{}}'
        for index, (name, kind) in enumerate(references, 1))
    (scene / "stage.usda").write_text(
        '#usda 1.0\n(defaultPrim = "World"\n metersPerUnit = 1\n upAxis = "Z")\n'
        'def Xform "World" {\n def Xform "Lab" {}\n def Xform "Dressing" {\n'
        + dressing + '\n }\n}\n', encoding="utf-8", newline="\n")
    print(json.dumps({"scene": "fr3-culture-reference@1", "assets": len(references),
                      "triangles": sum(c["triangles"] for c in recipe["chunks"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derived", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT / "assets/authoring/fr3-culture-lab/1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.derived.resolve(), args.source.resolve(), args.output.resolve())
